import unittest
from audit_baseline_compatibility import classify

class CompatibilityPolicyTests(unittest.TestCase):
    def test_reject_newer_declared_msrv(self):
        self.assertEqual(classify({'edition':'2021','rust-version':'1.71'},'1.65.0'),'blocked_declared_msrv')
    def test_reject_edition_2024(self):
        self.assertEqual(classify({'edition':'2024','rust-version':'1.88'},'1.66.0'),'blocked_edition')
    def test_missing_msrv_does_not_claim_compilation(self):
        self.assertEqual(classify({'edition':'2018'},'1.65.0'),'metadata_eligible_not_built')
if __name__=='__main__':unittest.main()
