import copy
import json
from pathlib import Path
import tempfile
import unittest

from coverage_snapshot import digest, freeze, measure, save, source_file


def fixture():
    def api(item_id, name, column, end_column):
        return {"id": item_id, "crate_id": 0, "name": name, "visibility": "public",
                "span": {"filename": "/registry/sample-1.2.3/src/lib.rs",
                         "begin": [1, column], "end": [1, end_column]},
                "deprecation": None,
                "inner": {"function": {"has_body": True, "sig": {"inputs": [], "output": None},
                                       "generics": {"params": [], "where_predicates": []}}}}
    return {"apis": [{"id": 1}, {"id": 2}], "parser": {"krate": {
        "root": 0, "crate_version": "1.2.3", "format_version": 57,
        "includes_private": False, "index": {
            "0": {"name": "sample", "crate_id": 0, "inner": {"module": {"items": [1, 2]}}},
            "1": api(1, "left", 1, 20), "2": api(2, "right", 21, 40)}}}}


def coverage(functions):
    return {"type": "llvm.coverage.json.export", "version": "3.0.1", "data": [
        {"files": [{"filename": "/elsewhere/sample-1.2.3/src/lib.rs"}], "functions": functions}]}


def function(column=1, end_column=20, count=1, name="symbol"):
    return {"name": name, "count": count,
            "filenames": ["/elsewhere/sample-1.2.3/src/lib.rs"],
            "regions": [[1, column, 1, end_column, count, 0, 0, 0]]}


class CoverageSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "doc.json"
        save(self.path, fixture())
        self.snapshot = freeze(self.path, "sample", ["right"])

    def tearDown(self):
        self.temp.cleanup()

    def test_same_line_functions_do_not_share_coverage(self):
        result = measure(self.snapshot, coverage([function()]))
        states = {r["name"]: r["status"] for r in result["apis"]}
        self.assertEqual(states, {"left": "covered", "right": "unresolved"})
        self.assertEqual(result["groups"]["historical_selected"]["total"], 2)

    def test_zero_count_is_distinct_from_missing_record(self):
        result = measure(self.snapshot, coverage([function(count=0)]))
        group = result["groups"]["historical_selected"]
        self.assertEqual((group["covered"], group["observed_zero"], group["unresolved"]), (0, 1, 1))

    def test_generic_instantiations_count_as_one_api(self):
        result = measure(self.snapshot, coverage([function(name="u8"), function(name="u32")]))
        self.assertEqual(result["groups"]["historical_selected"]["covered"], 1)

    def test_all_llvm_data_records_are_read(self):
        report = coverage([function(count=0)])
        report["data"].append(coverage([function(column=21, end_column=40)])["data"][0])
        self.assertEqual(measure(self.snapshot, report)["groups"]["historical_selected"]["covered"], 1)

    def test_shared_entry_span_is_ambiguous(self):
        doc = fixture()
        doc["parser"]["krate"]["index"]["2"]["span"] = copy.deepcopy(doc["parser"]["krate"]["index"]["1"]["span"])
        save(self.path, doc)
        result = measure(freeze(self.path, "sample", []), coverage([function()]))
        self.assertEqual(result["groups"]["historical_selected"]["covered"], 0)
        self.assertEqual(result["unmatched_functions"][0]["reason"], "ambiguous_entry")

    def test_mismatched_version_is_rejected(self):
        report = coverage([function()])
        report["data"][0]["files"][0]["filename"] = "/registry/sample-9.9.9/src/lib.rs"
        with self.assertRaisesRegex(ValueError, "different target crate version"):
            measure(self.snapshot, report)

    def test_tampered_snapshot_is_rejected(self):
        self.snapshot["apis"][0]["name"] = "changed"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            measure(self.snapshot, coverage([]))

    def test_segments_cannot_substitute_for_entry_counts(self):
        report = coverage([])
        del report["data"][0]["functions"]
        with self.assertRaisesRegex(ValueError, "function records missing"):
            measure(self.snapshot, report)

    def test_no_basename_fallback(self):
        self.assertIsNone(source_file("/different/src/lib.rs", "sample", "1.2.3"))

    def test_named_filter_is_separate_from_frozen_selected_pool(self):
        result = measure(self.snapshot, coverage([function(21, 40)]))
        self.assertEqual(result["groups"]["historical_selected"]["covered"], 1)
        self.assertEqual(result["groups"]["local_callable_candidates_name_filtered"]["covered"], 0)

    def test_freeze_rejects_mismatched_document_identity(self):
        with self.assertRaisesRegex(ValueError, "crate name"):
            freeze(self.path, "other", [])
        with self.assertRaisesRegex(ValueError, "crate version"):
            freeze(self.path, "sample", [], expected_version="9.9.9")

    def test_duplicate_apidoc_rows_are_reported(self):
        doc = fixture()
        doc["apis"].append({"id": 1})
        save(self.path, doc)
        snapshot = freeze(self.path, "sample", [])
        self.assertEqual(snapshot["historical_duplicate_rows"], 1)
        self.assertEqual(sum(a["selected_by_historical_doc"] for a in snapshot["apis"]), 2)
        result = measure(snapshot, coverage([function()]))
        self.assertEqual(result["groups"]["historical_selected_rows_with_duplicates"]["total"], 3)
        self.assertEqual(result["groups"]["historical_selected_rows_with_duplicates"]["covered"], 2)

    def test_function_only_version_mismatch_is_rejected(self):
        report = coverage([function()])
        report["data"][0]["functions"][0]["filenames"] = ["/registry/sample-9.9.9/src/lib.rs"]
        with self.assertRaisesRegex(ValueError, "different target crate version"):
            measure(self.snapshot, report)

    def test_relative_path_traversal_is_not_normalized(self):
        self.assertIsNone(source_file("/sample-1.2.3/../src/lib.rs", "sample", "1.2.3"))

    def test_legacy_line_control_exposes_same_line_overcount(self):
        report = coverage([function()])
        report["data"][0]["files"][0]["segments"] = [[1, 1, 1, True, True, False]]
        result = measure(self.snapshot, report)
        self.assertEqual(result["legacy_segment_diagnostic"]["covered_rows"], 2)
        self.assertEqual(result["groups"]["historical_selected"]["covered"], 1)

    def test_public_surface_groups_keep_unmapped_definitions_in_denominator(self):
        for api in self.snapshot["apis"]:
            api["public_name_body_definition"] = True
        self.snapshot["universe_sha256"] = digest({k: v for k, v in self.snapshot.items() if k != "universe_sha256"})
        group = measure(self.snapshot, coverage([function()]))["groups"]["public_name_body_definitions"]
        self.assertEqual((group["covered"], group["total"], group["unresolved"]), (1, 2, 1))


if __name__ == "__main__":
    unittest.main()
