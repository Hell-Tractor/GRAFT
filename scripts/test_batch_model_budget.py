import json,multiprocessing
from decimal import Decimal
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from batch_model_budget import initialize
from model_cost_control import read_policy,reserve,settle,read_ledger,spent,BudgetRejected
from costed_model_request import send

POLICY=Path(__file__).resolve().parents[1]/'experiments/cost-control/policy.json'
MESSAGES=[{'role':'user','content':'small prompt'}]


def reserve_process(args):
    root,index=args;root=Path(root);policy=read_policy(POLICY)
    try:
        reserve(MESSAGES,policy,root/f't{index}.json',root/f'c{index}.json',root/'batch.json','.30',f'GRAFT/c/{index}')
        return True
    except BudgetRejected:return False


class BatchBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.t=self.root/'t.json';self.c=self.root/'c.json';self.b=self.root/'batch.json'
        self.p=read_policy(POLICY)
    def reserve(self,**kwargs):
        return reserve(MESSAGES,self.p,self.t,self.c,self.b,'2.40',kwargs.get('key','GRAFT/c/0'))
    def test_shared_total_across_real_processes(self):
        with multiprocessing.get_context('fork').Pool(8) as pool:
            accepted=pool.map(reserve_process,[(str(self.root),i) for i in range(16)])
        ledger=read_ledger(self.b)
        self.assertLessEqual(spent(ledger),Decimal('.30'))
        self.assertEqual(len(ledger['entries']),sum(accepted));self.assertGreater(sum(accepted),0)
    def test_settlement_updates_all_three_and_releases_unused_reservation(self):
        _,ticket=self.reserve();before=spent(read_ledger(self.b))
        usage={'prompt_tokens':10,'completion_tokens':10}
        settle(ticket,usage,self.p,self.t,self.c,batch_path=self.b,batch_budget_cny='2.40')
        amounts=[spent(read_ledger(p)) for p in [self.t,self.c,self.b]]
        self.assertEqual(len(set(amounts)),1);self.assertLess(amounts[0],before)
    def test_unknown_retains_reservation_and_stops_batch(self):
        _,ticket=self.reserve();before=spent(read_ledger(self.b))
        settle(ticket,None,self.p,self.t,self.c,'Timeout',self.b,'2.40')
        self.assertEqual(spent(read_ledger(self.b)),before)
        with self.assertRaisesRegex(BudgetRejected,'unknown usage'):
            reserve(MESSAGES,self.p,self.root/'t2.json',self.root/'c2.json',self.b,'2.40','GRAFT/d/0')
    def test_deleted_subledgers_cannot_reset_call_count(self):
        for _ in range(2):
            _,ticket=self.reserve();settle(ticket,{'prompt_tokens':10,'completion_tokens':10},self.p,self.t,self.c,batch_path=self.b,batch_budget_cny='2.40')
        self.t.unlink();self.c.unlink()
        with self.assertRaisesRegex(BudgetRejected,'maximum paid calls'):self.reserve()
        with self.assertRaisesRegex(BudgetRejected,'cannot move'):
            reserve(MESSAGES,self.p,self.root/'new-target.json',self.c,self.b,'2.40','GRAFT/c/0')
    def test_crash_after_authority_write_retains_entire_reservation(self):
        import batch_model_budget
        original=batch_model_budget.store
        def crash(path,value):
            if path==self.c.resolve():raise OSError('simulated crash')
            original(path,value)
        with patch('batch_model_budget.store',side_effect=crash):
            with self.assertRaises(OSError):self.reserve()
        cost=spent(read_ledger(self.b));self.assertGreater(cost,0)
        _,ticket=self.reserve();self.assertEqual(len(read_ledger(self.b)['entries']),2)
        self.assertGreater(spent(read_ledger(self.b)),cost)
        with self.assertRaisesRegex(BudgetRejected,'maximum paid calls'):self.reserve()
    def test_limit_policy_and_batch_identity_cannot_change(self):
        self.reserve()
        for cap in ['2.41','NaN','0','-1']:
            with self.assertRaises((BudgetRejected,ValueError)):
                reserve(MESSAGES,self.p,self.t,self.c,self.b,cap,'GRAFT/c/0')
        cloned=self.root/'clone.json';cloned.write_bytes(self.b.read_bytes())
        with self.assertRaisesRegex(BudgetRejected,'identity'):
            reserve(MESSAGES,self.p,self.t,self.c,cloned,'2.40','GRAFT/c/0')
    def test_cannot_bypass_or_downgrade_batch_ledger(self):
        _,ticket=self.reserve()
        with self.assertRaises(BudgetRejected):reserve(MESSAGES,self.p,self.t,self.c)
        settle(ticket,{'prompt_tokens':10,'completion_tokens':10},self.p,self.t,self.c,batch_path=self.b,batch_budget_cny='2.40')
        with self.assertRaises(BudgetRejected):settle(ticket,None,self.p,self.t,self.c,batch_path=self.b,batch_budget_cny='2.40')
    def test_batch_exhaustion_rejects_before_network(self):
        with patch('urllib.request.urlopen') as network:
            with self.assertRaises(BudgetRejected):
                send(MESSAGES,self.p,self.t,self.c,self.root/'request.json',self.root/'response.json',credential='test-only',batch_ledger=self.b,batch_budget_cny='.001')
            network.assert_not_called()
    def test_usage_overrun_is_recorded_and_blocks_further_calls(self):
        _,ticket=self.reserve()
        with self.assertRaisesRegex(BudgetRejected,'exceeded reservation'):
            settle(ticket,{'prompt_tokens':100000,'completion_tokens':100000},self.p,self.t,self.c,batch_path=self.b,batch_budget_cny='2.40')
        self.assertTrue(read_ledger(self.b)['entries'][0]['reservation_exceeded'])
        with self.assertRaises(BudgetRejected):self.reserve()


class CalibrationStopTests(unittest.TestCase):
    def test_calibration_stops_and_retains_unrun_slots(self):
        from calibrate_parameter_repair import calibrate
        from admission_diagnostics import atomic_json
        import hashlib
        for reason in ['unknown','exhausted']:
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=root/'source';raw={'index':{'x':{'inner':{'function':{'sig':{'inputs':[]}}}}}}
                digest=hashlib.sha256(json.dumps(raw,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
                plan={'target':'x','crate_name':'fixture','crate_version':'1','rustdoc_sha256':digest,
                    'steps':[{'kind':'call','api':'x','path':'fixture::x','args':[]}]}
                atomic_json(source/'runtime/raw-rustdoc/fixture-1.json',raw)
                atomic_json(source/'plans/round-01/fixture/catalog.json',{'apis':[{'id':'x','path':'x','owner':None}]})
                for slot in [0,1]:
                    d=source/'preparation/round-01/GRAFT/fixture'/str(slot)
                    atomic_json(d/'generation.json',{'finished':True,'accepted':False,'status':'generation_failed'})
                    atomic_json(d/'emitted/plan.json',plan)
                tools=[]
                for name in ['planner','harness']:
                    p=root/name;p.write_text('test-only binary');tools.append(p)
                calls=[]
                class Pool:
                    def shutdown(self,**kw):pass
                class Fake:
                    def __init__(self,output,**kw):
                        self.root=output;self.generation={};self.requests=0;self.fuzz_pool=Pool();self.fuzz_futures=[]
                    def directory(self,m,c,i):
                        p=self.root/'preparation/round-01'/m/c/str(i);p.mkdir(parents=True,exist_ok=True);return p
                    def model(self,m,c,i,*args,**kw):
                        calls.append(i);d=self.directory(m,c,i)
                        entry={'id':'test','status':'unknown_charge_reserved','charged_cny':'.10'}
                        entries=[entry] if reason=='unknown' else []
                        self.requests=len(entries)
                        atomic_json(d/'model-cost.json',{'entries':entries})
                        atomic_json(self.root/'batch-model-cost.json',{'entries':entries})
                        record={'finished':True,'accepted':False,'status':'infrastructure_failed','attempts':[{'index':0}]}
                        if reason=='exhausted':record['budget_rejection']='GRAFT_BATCH_EXHAUSTED: test'
                        self.generation[(m,c,i)]=record;atomic_json(d/'generation.json',record)
                with patch('calibrate_parameter_repair.Engine',Fake):
                    result=calibrate(source,root/'out',*tools,True,[('fixture',0),('fixture',1)],batch_budget_cny='2.40')
                self.assertEqual(calls,[0]);self.assertEqual(result['status'],'calibration_stopped')
                self.assertEqual(result['cases'],2);self.assertEqual(result['executed_cases'],1)
                self.assertEqual(result['results'][1]['status'],'not_run_batch_stopped')

class CalibrationReviewTests(unittest.TestCase):
    def test_actual_compiler_duration_field_is_reported(self):
        from review_repair_calibration import timing_metrics
        result=timing_metrics([{'attempts':[{'compiler_runs':[{'seconds':.5},{'seconds':.75}]}]}])
        self.assertEqual(result['compiler_process_seconds']['n'],2)
        self.assertEqual(result['compiler_process_seconds']['sum_seconds'],1.25)
        self.assertEqual(result['attempt_compiler_total_seconds']['median_seconds'],1.25)
    def test_no_compile_is_not_reported_as_zero_second_compile(self):
        from review_repair_calibration import timing_metrics
        result=timing_metrics([{'attempts':[{}]}])
        self.assertEqual(result['compiler_process_seconds']['n'],0)
        self.assertIsNone(result['attempt_compiler_total_seconds']['median_seconds'])

if __name__=='__main__':unittest.main()
