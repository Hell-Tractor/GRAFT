import copy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_cost_control import (BudgetRejected, compact_controlled, compact_native, compact_diagnostics,
    prepare_body, read_ledger, read_policy, reserve, settle, spent, usage_cost)
from costed_model_request import send

POLICY=Path(__file__).resolve().parents[1]/'experiments/cost-control/policy.json'


class CostControlTests(unittest.TestCase):
    def setUp(self):self.policy=read_policy(POLICY)

    def test_peak_price_cache_and_reasoning_are_not_double_counted(self):
        u={'prompt_tokens':10000,'prompt_cache_hit_tokens':8000,'completion_tokens':1000,
           'completion_tokens_details':{'reasoning_tokens':900}}
        self.assertEqual(usage_cost(u,self.policy),Decimal('.0474'))

    def test_missing_usage_is_unknown_and_missing_cache_is_a_miss(self):
        self.assertIsNone(usage_cost({},self.policy))
        self.assertEqual(usage_cost({'prompt_tokens':1000,'completion_tokens':1000},self.policy),Decimal('.036'))

    def test_thinking_disabled_and_dynamic_response_cap(self):
        body,estimate=prepare_body([{'role':'user','content':'a'*1000}],self.policy,Decimal('.04'))
        self.assertEqual(body['thinking'],{'type':'disabled'})
        self.assertNotIn('reasoning_effort',body)
        self.assertLess(body['max_tokens'],self.policy['max_output_tokens'])
        self.assertLessEqual(Decimal(estimate['reserved_cny']),Decimal('.04'))

    def test_reject_context_locally_without_touching_source(self):
        source='IMMUTABLE CORE:\n'+('x'*15000)
        with self.assertRaises(BudgetRejected):prepare_body([{'role':'user','content':source}],self.policy,Decimal('.24'))

    def test_compiler_errors_keep_messages_without_expanded_source(self):
        log=json.dumps({'level':'warning','message':'noise','spans':[{'text':'x'*10000}]})+'\n'+json.dumps({'level':'error','message':'method chunk_mut does not exist','code':{'code':'E0599'},'spans':[{'text':'y'*10000}]})
        message=compact_diagnostics(log,500)
        self.assertIn('E0599',message);self.assertIn('chunk_mut',message)
        self.assertNotIn('yyyy',message);self.assertNotIn('noise',message)

    def test_plan_and_docs_compaction_preserves_core(self):
        prompt='Public documentation pool:\n'+json.dumps([{'path':'a::needed','docs':'short'},{'path':'a::other','docs':'x'*20000}])+'\nRender instructions\nPLAN:\n{"large":true}\nIMMUTABLE CORE:\nfn graft_body(){a::needed();}'
        result=compact_controlled(prompt,self.policy)
        self.assertIn('fn graft_body(){a::needed();}',result)
        self.assertNotIn('"large"',result)
        self.assertIn('Render instructions',result)
        self.assertLess(len(result.encode()),len(prompt.encode()))

    def test_native_compaction_keeps_harness_and_system(self):
        text='=== SAMPLE HARNESS START ===\nfn main(){bytes::Bytes::new();}\n=== SAMPLE HARNESS END ===\n=== STATIC ANALYSIS START ===\n'+json.dumps({'needed':{'call':'bytes::Bytes::new'},'huge':'x'*30000})+'\n=== STATIC ANALYSIS END ==='
        messages=[{'role':'system','content':'all native safety constraints'},{'role':'user','content':text}]
        result=compact_native(messages,self.policy)
        self.assertEqual(result[0],messages[0]);self.assertIn('fn main(){bytes::Bytes::new();}',result[1]['content'])
        self.assertNotIn('x'*30000,result[1]['content'])

    def test_timeout_reservation_survives_resume_and_blocks_overspend(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=Path(tmp)/'target.json';c=Path(tmp)/'crate.json';msgs=[{'role':'user','content':'x'*14000}]
            _,ticket=reserve(msgs,self.policy,t,c);settle(ticket,None,self.policy,t,c,'TimeoutError')
            self.assertGreater(spent(read_ledger(t)),Decimal('.2'))
            with self.assertRaises(BudgetRejected):reserve(msgs,self.policy,t,c)

    def test_repairs_and_crate_limits_cannot_be_reset_by_new_process(self):
        p=copy.deepcopy(self.policy);p['max_llm_targets_per_crate']=1
        with tempfile.TemporaryDirectory() as tmp:
            t=Path(tmp)/'target.json';c=Path(tmp)/'crate.json';msgs=[{'role':'user','content':'small'}]
            for _ in range(2):
                _,ticket=reserve(msgs,p,t,c);settle(ticket,{'prompt_tokens':10,'completion_tokens':10},p,t,c)
            with self.assertRaises(BudgetRejected):reserve(msgs,p,t,c)
            t.unlink()  # An external balance-retry archive is not a new budget.
            with self.assertRaises(BudgetRejected):reserve(msgs,p,t,c)
            with self.assertRaises(BudgetRejected):reserve(msgs,p,Path(tmp)/'another.json',c)

    def test_native_optional_cards_shrink_before_essential_source(self):
        p=copy.deepcopy(self.policy);p['max_prompt_utf8_bytes']=1000
        harness='fn main(){bytes::Bytes::new();}'
        messages=[{'role':'system','content':'constraints'*50},{'role':'user','content':'=== SAMPLE HARNESS START ==='+harness+'=== SAMPLE HARNESS END ===\n=== DOCUMENTATION START ==='+json.dumps([{'path':'a','docs':'d'*600}])+'=== DOCUMENTATION END ==='}]
        result=compact_native(messages,p)
        self.assertLessEqual(sum(len(m['content'].encode()) for m in result),1000)
        self.assertEqual(result[0],messages[0]);self.assertIn(harness,result[1]['content'])

    def test_refusal_never_calls_network(self):
        with tempfile.TemporaryDirectory() as tmp,patch('urllib.request.urlopen') as network:
            r=Path(tmp)
            with self.assertRaises(BudgetRejected):send([{'role':'user','content':'x'*15000}],self.policy,r/'t.json',r/'c.json',r/'req.json',r/'response.json',credential='test-only')
            network.assert_not_called()


if __name__=='__main__':unittest.main()
