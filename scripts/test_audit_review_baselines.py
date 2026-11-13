import json
import tempfile
import unittest
from pathlib import Path

import audit_review_baselines as audit


class ReviewBaselineAuditTests(unittest.TestCase):
    def test_totals_retain_missing_slots_and_do_not_impute_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary = {
                "status": "incomplete",
                "rows": [
                    {
                        "crate": "a",
                        "status": "complete",
                        "slot_status_counts": {"measured": 18, "validation_rejected": 2},
                        "denominator": 10,
                        "confirmed_covered_lower_bound": 2,
                        "measured_targets": 18,
                        "coverage_percent": 20.0,
                    },
                    {
                        "crate": "b",
                        "status": "incomplete",
                        "slot_status_counts": {
                            "measured": 5,
                            "replay_failed": 3,
                            "infrastructure_failed": 2,
                            "no_target": 10,
                        },
                        "denominator": 30,
                        "confirmed_covered_lower_bound": 3,
                        "measured_targets": 5,
                        "coverage_percent": None,
                    },
                ],
            }
            (root / "summary.json").write_text(json.dumps(summary))
            result = audit.audit_track(
                "Example", root, ["summary.json"], {"a": "1", "b": "1"}
            )

            self.assertEqual(result["fixed_slots"], 40)
            self.assertEqual(result["accounted_slots"], 40)
            self.assertEqual(result["measured_slots"], 23)
            self.assertEqual(result["failure_gap_slots"], 17)
            self.assertEqual(result["confirmed_covered_definitions_union"], 5)
            self.assertEqual(result["defined_api_denominator_union"], 40)
            self.assertEqual(result["pooled_coverage_lower_bound_percent"], 12.5)
            self.assertEqual(result["complete_row_macro_coverage_percent"], 20.0)
            self.assertEqual(result["complete_crate_rows"], 1)

    def test_missing_track_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = audit.audit_track(
                "Example", Path(temporary), ["summary.json"], {"a": "1"}
            )
        self.assertEqual(result["status"], "missing")
        self.assertNotIn("measured_slots", result)


if __name__ == "__main__":
    unittest.main()
