import json,tempfile,unittest
from pathlib import Path
from admission_diagnostics import atomic_json
from parameter_repair import read_rules,rule_context,base_prompt,prompt
from repair_context import task
from model_cost_control import read_policy,compact_controlled,prepare_body
from decimal import Decimal

ROOT=Path(__file__).resolve().parents[1]

class CloseoutTests(unittest.TestCase):
    def plan(self,crate,version,paths):
        return {'target':'target','crate_name':crate,'crate_version':version,'steps':[
            {'kind':'call','api':'target' if i==len(paths)-1 else str(i),'instance':i,'path':p,'args':[]}
            for i,p in enumerate(paths)]}
    def test_all_time_guidance_survives_budget_and_wrong_owner_is_excluded(self):
        rules=read_rules(ROOT/'experiments/admission-v2/parameter-hints-v3.json')
        p=self.plan('time','0.3.9',['<time::Date>::from_ordinal_date','<time::Time>::from_hms',
            '<time::UtcOffset>::from_hms','<time::OffsetDateTime>::replace_minute'])
        rows=rule_context(p,rules);ids={r['id']for r in rows}
        self.assertTrue({'time-short-scalar-v3','time-date-v1','time-clock-offset-v3'}<=ids)
        p['steps'][-1]['path']='<time::Date>::replace_minute'
        self.assertNotIn('time-short-scalar-v3',{r['id']for r in rule_context(p,rules)})
        p['crate_version']='0.3.10';self.assertEqual(rule_context(p,rules),[])
    def test_scalar_diagnosis_does_not_blame_changing_receiver(self):
        p=self.plan('time','0.3.9',['<time::OffsetDateTime>::replace_minute'])
        samples=[{'target_reached':True,'exit_code':0,'scalar_arguments':['(0,)']}for _ in range(3)]
        change=task(p,'source',{'code':'input_influence_unobserved','samples':samples})['required_local_change']
        self.assertIn('scalar',change);self.assertIn('unwrap_or(b)',change);self.assertNotIn('object is constant',change)
        for s in samples:s['scalar_arguments']=['()']
        self.assertIn('SAME producer',task(p,'source',{'code':'input_influence_unobserved','samples':samples})['required_local_change'])
    def test_actual_request_keeps_safe_preparation_and_diagnostic(self):
        cases=[('csv','1.3.1',['<csv::StringRecord>::new','<csv::StringRecord>::into_byte_record'],
                {'stage':'probe','code':'runtime_panic','message':'range start index 1 out of range'},'data[1..1]',['data.get(start..end)','push_field']),
            ('bumpalo','3.14.0',['<bumpalo::Bump>::new','<bumpalo::Bump>::allocated_bytes_including_metadata'],
                {'stage':'normalization','code':'contract_rejected','message':'explicit loops are not supported; macros/trace'},'source',['alloc_slice_copy','No temporary Vec'])]
        rules=read_rules(ROOT/'experiments/admission-v2/parameter-hints-v3.json');policy=read_policy(ROOT/'experiments/cost-control/policy.json')
        with tempfile.TemporaryDirectory() as tmp:
            for c,v,paths,diag,source,expected in cases:
                p=Path(tmp)/'plan.json';atomic_json(p,{'plan':self.plan(c,v,paths)})
                base=base_prompt(p,'Public documentation pool:\n[]\nRender the following pinned SequencePlan',rules)
                for text in [base,prompt(base,source,diag,p)]:
                    body,_=prepare_body([{'role':'user','content':compact_controlled(text,policy)}],policy,Decimal('.24'))
                    transmitted=body['messages'][0]['content']
                    for phrase in expected:self.assertIn(phrase,transmitted)
                    self.assertLessEqual(len(transmitted.encode()),policy['max_prompt_utf8_bytes'])
                if c=='csv':self.assertIn('data[1..1]',task(self.plan(c,v,paths),source,diag)['required_local_change'])

if __name__=='__main__':unittest.main()
