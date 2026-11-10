import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from launch_controlled_round import clean_env
from model_cost_control import compact_controlled, read_policy
from parameter_repair import base_prompt,prompt,read_rules,rule_context
from admission_diagnostics import atomic_json,fingerprint
from model_source import extract
from replay_repair_attempts import collect
from repair_context import classification,task,documentation_card,type_spelling

class ExtractionTests(unittest.TestCase):
    def test_plain_and_one_block(self):
        code='extern crate alloc; pub fn graft_body() {}'
        self.assertEqual(extract(code)[0],code)
        got,receipt=extract('Explanation\n```rust\n'+code+'\n```\nEnd')
        self.assertEqual(got,code);self.assertEqual(receipt['kind'],'unique_rust_block')
    def test_reject_ambiguous_or_unclosed(self):
        for source in ['```rust\nx\n','```rust\nx\n```\n```rust\ny\n```','```python\nx\n```']:
            with self.assertRaises(ValueError):extract(source)
    def test_verified_plain_rust_is_never_extracted_from_a_string_literal(self):
        code='pub fn graft_body(){let s=r#"\n```rust\nfn fake(){}\n```\n"#;}'
        self.assertEqual(extract(code,validated_plain=True)[0],code)

    def test_does_not_fix_invalid_or_unsafe_rust(self):
        self.assertIn('unsafe',extract('```rust\nunsafe { bogus() };\n```')[0])

class PairingTests(unittest.TestCase):
    def test_actual_plan_per_attempt_and_no_repair_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);d=root/'preparation/round-01/GRAFT/fixture/0';d.mkdir(parents=True)
            p={'target':'id','crate_name':'fixture','crate_version':'1','package_name':'fixture','features':[],
               'default_features':True,'rustdoc_sha256':'doc','steps':[]}
            atomic_json(d/'plan.json',{'plan':p})
            alt=copy.deepcopy(p);alt['steps']=[{'new':'producer'}];atomic_json(d/'alt.json',{'plan':alt})
            for i in range(2):(d/f'attempt-{i}.rs').write_text('source '+str(i))
            record={'crate':'fixture','slot':0,'finished':True,'attempts':[{'index':0},{'index':1,'plan':fingerprint(d/'alt.json')}]}
            atomic_json(d/'generation.json',record)
            rows=collect(root);self.assertEqual(rows[1]['plan'],fingerprint(d/'alt.json'))
            del record['attempts'][1]['plan'];atomic_json(d/'generation.json',record)
            with self.assertRaisesRegex(ValueError,'Missing actual plan'):collect(root)
    def test_changed_plan_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);d=root/'preparation/round-01/GRAFT/fixture/0';d.mkdir(parents=True)
            atomic_json(d/'plan.json',{'plan':{'target':'id'}})
            (d/'attempt-0.rs').write_text('source')
            record={'crate':'fixture','slot':0,'finished':True,'attempts':[{'index':0,'plan':fingerprint(d/'plan.json')}]}
            atomic_json(d/'generation.json',record);atomic_json(d/'plan.json',{'plan':{'target':'changed'}})
            with self.assertRaisesRegex(ValueError,'Changed attempt plan'):collect(root)

class RepairTaskTests(unittest.TestCase):
    def plan(self,args=1):
        return {'target':'x','crate_name':'time','crate_version':'0.3.9','steps':[
            {'kind':'call','path':'<time::Date>::from_ordinal_date','api':'x','args':[{}]*args}]}
    def test_no_inputs_does_not_exempt_receiver(self):
        self.assertEqual(classification(self.plan())['influence_applicability'],'required')
        self.assertEqual(classification(self.plan(0))['influence_applicability'],'not_applicable_no_arguments')
    def test_borrow_and_empty_input_are_concrete(self):
        self.assertIn('BEFORE',task(self.plan(),'x',{'code':'core_compile_error','message':'E0502'})['required_local_change'])
        self.assertIn('data.get',task(self.plan(),'data[0]',{'code':'runtime_panic'})['required_local_change'])
        self.assertIn('ordinal',task(self.plan(),'x',{'code':'target_not_reached'})['required_local_change'])

class BudgetAndEnvironmentTests(unittest.TestCase):
    def test_compact_documentation_retains_helper_with_target(self):
        root=Path(__file__).resolve().parents[1];policy=read_policy(root/'experiments/cost-control/policy.json')
        api={'id':'target','path':'into_byte_record','owner':None,'inputs':[],'output':{'kind':'tuple','items':[]}}
        target=documentation_card(api,'long docs '*1000,0)
        helper=documentation_card(dict(api,id='helper',path='push_field'),'helper docs '*1000,1)
        text=compact_controlled('Public documentation pool:\n'+json.dumps([target,helper])+'\nRender the following pinned SequencePlan',policy)
        self.assertIn('push_field',text);self.assertIn('into_byte_record',text)
        self.assertEqual(type_spelling({'kind':'tuple','items':[]}),'()')
        self.assertTrue(target['documentation_truncated_for_budget'])
    def test_observation_flags_are_removed_from_normal_environment(self):
        with patch.dict('os.environ',{'GRAFT_TRACE':'1','GRAFT_PROBE_ARGS':'1','GRAFT_API_KEY':'test'}):
            env=clean_env()
            self.assertNotIn('GRAFT_TRACE',env);self.assertNotIn('GRAFT_PROBE_ARGS',env)
            self.assertNotIn('GRAFT_API_KEY',env)
    def test_repair_instruction_survives_real_budget_compaction(self):
        root=Path(__file__).resolve().parents[1]
        policy=read_policy(root/'experiments/cost-control/policy.json')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'plan.json'
            atomic_json(p,{'plan':{'target':'x','crate_name':'fixedbitset','crate_version':'0.5.7',
                'steps':[{'kind':'call','api':'x','instance':0,'path':'<fixedbitset::FixedBitSet>::set','args':[]}]}})
            base=base_prompt(p,'Public documentation pool:\n[]\nRender the following pinned SequencePlan')
            text=compact_controlled(prompt(base,'source',{'code':'core_compile_error','stage':'candidate','message':'E0502'},p),policy)
            self.assertIn('BEFORE',text);self.assertIn('ACTIVE PLANNED CALLS',text)
            self.assertLessEqual(len(text.encode()),policy['max_prompt_utf8_bytes'])
    def test_new_guidance_keeps_versions_and_owner_roles_isolated(self):
        root=Path(__file__).resolve().parents[1]
        rules=read_rules(root/'experiments/admission-v2/parameter-hints-v2.json')
        p={'crate_name':'time','crate_version':'0.3.9','steps':[{'kind':'call','instance':0,'path':'<time::Date>::from_ordinal_date'}]}
        self.assertTrue(any('year=2000' in r['advice'] for r in rule_context(p,rules)))
        p['crate_version']='0.3.10';self.assertEqual(rule_context(p,rules),[])

if __name__=='__main__':unittest.main()
