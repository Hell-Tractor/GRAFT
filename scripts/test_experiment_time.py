import unittest
from estimate_experiment_time import estimate,scenarios,waves

class ExperimentTimeTests(unittest.TestCase):
    def test_master_follower_charged(self):
        r=estimate(scenarios()[0],64,24,6,2)
        self.assertEqual(r['harness_runs'],10400)
        self.assertEqual(r['worker_runs'],13000)
        self.assertEqual(r['30_min_fuzz_lower_bound_hours'],101.5625)
    def test_bundle_cannot_split(self):
        self.assertEqual(waves([{'runs':3,'workers':2}],3),3)
    def test_duration_budget_includes_reserves(self):
        r=estimate(scenarios()[0],56,24,6,2)
        duration=r['max_seconds_per_harness_within_deadline']
        self.assertLessEqual(duration*r['equal_duration_waves']/3600+8,24)
        self.assertGreater((duration+1)*r['equal_duration_waves']/3600+8,24)
    def test_no_feasible_bundle(self):
        with self.assertRaises(ValueError):waves([{'runs':1,'workers':2}],1)
    def test_full_not_replaced_by_reduced_scope(self):
        full,extended,pilot=scenarios()
        self.assertEqual(sum(g['runs'] for g in extended['groups']),28600)
        self.assertLess(sum(g['runs'] for g in pilot['groups']),sum(g['runs'] for g in full['groups']))
if __name__=='__main__':unittest.main()
