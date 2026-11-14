from pathlib import Path
import tempfile
import unittest

from audit_experiments import inventory, lock_packages, stats_record
from coverage_snapshot import file_hash


class ArtifactAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_coverage_is_invalid_evidence_not_zero(self):
        coverage = self.root / "coverage_detail.json"
        coverage.touch()
        before = file_hash(coverage)
        result = inventory(self.root, "sample")
        self.assertIn("coverage_error", result)
        self.assertNotIn("covered", result)
        self.assertEqual(file_hash(coverage), before)

    def test_missing_artifact_directory_stays_explicitly_missing(self):
        result = inventory(self.root / "missing", "sample")
        self.assertFalse(result["exists"])
        self.assertNotIn("coverage_source_versions", result)

    def test_version_and_archive_checksum_come_from_target_lock(self):
        path = self.root / "Cargo.lock"
        path.write_text('version = 4\n[[package]]\nname = "sample"\nversion = "1.2.3"\nchecksum = "abc"\n'
                        '[[package]]\nname = "sample-driver"\nversion = "0.1.0"\n')
        self.assertEqual(lock_packages(path, "sample"), [{"name": "sample", "version": "1.2.3", "checksum": "abc"}])

    def test_target_presence_and_surviving_worker_duration_are_distinct(self):
        target = self.root / "testcases/sample_test0"
        target.mkdir(parents=True)
        (target / "Cargo.toml").write_text('[package]\nname = "sample_test0"\nversion = "0.1.0"\n')
        stats = target / "outputs/default/fuzzer_stats"
        stats.parent.mkdir(parents=True)
        stats.write_text('run_time : 7200\ncommand_line : afl-fuzz -V 3600\n')
        before = stats.read_bytes()
        result = inventory(self.root, "sample")
        self.assertEqual(len(result["targets"]), 1)
        self.assertEqual(len(result["worker_stats"]), 1)
        self.assertEqual(result["surviving_stats_run_time_seconds_sum"], 7200)
        self.assertNotIn("compiled", result)
        self.assertIn("not CPU time", result["budget_limitations"])
        self.assertEqual(stats.read_bytes(), before)

    def test_no_stats_does_not_mean_zero_worker_budget(self):
        result = inventory(self.root, "sample")
        self.assertIsNone(result["surviving_stats_run_time_seconds_sum"])


if __name__ == "__main__":
    unittest.main()
