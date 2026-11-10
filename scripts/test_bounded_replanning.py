import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from admission_diagnostics import atomic_json, fingerprint
from bounded_replanning import choose
from parameter_repair import read_rules, rule_context
from preflight_admission import read_profile


class ReplanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.original=self.root/'original.json'
        self.plan={'target':'fixed-target','crate_name':'fixture','crate_version':'1.0',
            'package_name':'fixture','features':[],'default_features':True,'rustdoc_sha256':'same-doc',
            'steps':[]}
        atomic_json(self.original,{'status':'planned','plan':self.plan})
        self.bounds={'max_candidates':3,'wall_seconds':1,'search_nodes':100}
        self.work=self.root/'target/replanning'

    def report(self):
        candidate=copy.deepcopy(self.plan);candidate['steps']=[{'changed_producer':True}]
        return {'target':'fixed-target','search_nodes':5,'candidates':[
            {'excluded_producer':'old-producer','result':{'status':'planned','plan':candidate}}]}

    def test_original_target_and_paid_ledger_survive_candidate_and_resume(self):
        ledger=self.root/'target/model-cost.json';atomic_json(ledger,{'entries':[{'paid':'kept'}]})
        before=fingerprint(ledger);initial=fingerprint(self.original)
        generate=Mock(return_value=self.report());preflight=Mock(return_value={'status':'passed'})
        chosen=choose(self.original,self.work,self.bounds,generate,preflight)
        self.assertEqual(json.loads(chosen.read_text())['plan']['target'],'fixed-target')
        self.assertEqual(choose(self.original,self.work,self.bounds,generate,preflight),chosen)
        self.assertEqual(generate.call_count,1);self.assertEqual(preflight.call_count,1)
        self.assertEqual(before,fingerprint(ledger));self.assertEqual(initial,fingerprint(self.original))
        state=json.loads((self.work/'plan-lineage.json').read_text())
        self.assertFalse(state['paid_budget_reset']);self.assertEqual(len(state['entries']),2)

    def test_changed_target_is_rejected_before_preflight(self):
        report=self.report();report['candidates'][0]['result']['plan']['target']='easier-target'
        preflight=Mock()
        with self.assertRaisesRegex(ValueError,'pinned identity'):
            choose(self.original,self.work,self.bounds,lambda *args:report,preflight)
        preflight.assert_not_called()
        generate=Mock()
        self.assertIsNone(choose(self.original,self.work,self.bounds,generate,preflight))
        generate.assert_not_called()

    def test_wall_budget_and_failed_candidate_are_not_reset(self):
        bounds=dict(self.bounds,wall_seconds=.002)
        def slow(*args):time.sleep(.01);return {'status':'passed'}
        generate=Mock(return_value=self.report())
        self.assertIsNone(choose(self.original,self.work,bounds,generate,slow))
        self.assertIsNone(choose(self.original,self.work,bounds,generate,slow))
        self.assertEqual(generate.call_count,1)
        self.assertEqual(json.loads((self.work/'plan-lineage.json').read_text())['status'],'wall_budget_exhausted')

    def test_inflight_state_and_oversized_candidate_list_fail_closed(self):
        report=self.report();report['candidates']*=3
        with self.assertRaisesRegex(ValueError,'candidate limit'):
            choose(self.original,self.work,self.bounds,lambda *args:report,Mock())
        state=json.loads((self.work/'plan-lineage.json').read_text());state['inflight']=True
        atomic_json(self.work/'plan-lineage.json',state)
        generate=Mock()
        with self.assertRaisesRegex(ValueError,'reconciled'):
            choose(self.original,self.work,self.bounds,generate,Mock())
        generate.assert_not_called()

    def test_versioned_rules_have_bounded_context_and_do_not_leak_to_other_versions(self):
        root=Path(__file__).resolve().parents[1]
        rules=read_rules(root/'experiments/admission-v2/parameter-hints-v1.json')
        plan={'crate_name':'fixedbitset','crate_version':'0.5.7','steps':[
            {'kind':'call','path':'<fixedbitset::FixedBitSet>::toggle','instance':1}]}
        context=rule_context(plan,rules)
        self.assertIn('grow',context[0]['advice'])
        self.assertLessEqual(len(json.dumps(context,ensure_ascii=False).encode()),2200)
        plan['crate_version']='0.5.8';self.assertEqual(rule_context(plan,rules),[])
        profile=read_profile(root/'experiments/admission-v2/development-complete.json')
        self.assertEqual(profile['repair_contract'],'planned-call-v3')
        for name,field in [('no-replanning','replanning'),('no-guidance','semantics_rules')]:
            ablation=read_profile(root/f'experiments/admission-v2/development-{name}.json')
            ignored={field,'revision','notes'}
            self.assertEqual({k:v for k,v in profile.items() if k not in ignored},
                             {k:v for k,v in ablation.items() if k not in ignored})
        empty=read_rules(root/'experiments/admission-v2/parameter-hints-disabled-v1.json')
        plan['crate_version']='0.5.7'
        self.assertEqual(rule_context(plan,empty),[])


if __name__=='__main__':unittest.main()
