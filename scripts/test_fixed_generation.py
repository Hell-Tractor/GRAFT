import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import run_fixed_generation as fixed
from run_experiment_rounds import save,load,file_hash,summarize,seed_for,execute

class FixedGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.batch=self.root/'batch';self.paper=self.root/'paper'
        self.batch.mkdir();self.calls=[];self.builds=[]
        self.u={'apis':[{'api_id':'a','public_name_body_definition':True}]}
        u=self.batch/'u.json';save(u,self.u)
        p={'methods':['GRAFT','RPG'],'versions':{'fixture':'1'},'target_slots':2,
            'seconds_per_target':300,'workers_per_target':1,'fuzz_workers':2,'rounds':6,
            'root_seed':17,'generation_mode':fixed.MODE,'generation_repetitions':1,'fuzz_repetitions':6,
            'afl_seeds_per_generation':6,'universes':{'fixture':fixed.artifact(u)},
            'publication_namespace':'fixed-unit-fixture','tectonic':'test-build','tectonic_cache':str(self.root)}
        save(self.batch/'protocol.json',p);p['_file_sha256']=file_hash(self.batch/'protocol.json');self.p=p
        save(self.batch/'fixed-generation-inputs.json',{'protocol':fixed.artifact(self.batch/'protocol.json')})
        save(self.batch/'control.json',{'pause':False,'stop_after_round':6,'auto_second':False})
        self.tasks=[]
        for m in p['methods']:
            for i in range(2):
                d=self.batch/'prepared'/m/str(i);d.mkdir(parents=True)
                task={'method':m,'crate':'fixture','version':'1','slot':i,'status':'ready','workers':1}
                if m=='RPG' and i==1:
                    save(d/'failed.json',{'status':'no_target'});task.update(status='no_target',evidence=fixed.artifact(d/'failed.json'))
                else:
                    for name in ['binary','source','validity_evidence','build_evidence','replay_binary','replay_adapter']:
                        f=d/name;f.write_text(name);task[name]=fixed.artifact(f)
                    initial=d/'initial';initial.mkdir();(initial/'0').write_bytes(b'0')
                    save(d/'initial.json',{'files':[fixed.artifact(initial/'0')]});task['initial_corpus_manifest']=fixed.artifact(d/'initial.json')
                    task.update(unsafe_contracts_reviewed=True,wrapper_probe_passed=True,shared_lock_features_verified=True,instrumentation_cfg_verified=True)
                    task['fuzz_command']=['afl','-i',str(initial),'-o','{run_dir}/afl','--',task['binary']['path']]
                    task['replay_command']=['python',task['replay_adapter']['path'],'--initial',str(initial),'--afl-output','{run_dir}/afl','--binary',task['replay_binary']['path'],'--work','{run_dir}/replayed']
                self.tasks.append(task)
        save(self.batch/'round-01/tasks.json',{'round':1,'protocol_sha256':p['_file_sha256'],'tasks':self.tasks})
        self.first=[dict(method=t['method'],crate=t['crate'],slot=t['slot'],status='measured' if t['status']=='ready' else t['status'],covered_api_ids=['a'] if t['status']=='ready' else [])for t in self.tasks]
        save(self.batch/'round-01/summary.json',summarize(p,self.first,{'fixture':self.u},1))
        rows=[{'method':m,'crate':'fixture','fixed_target_slots':2,'target_slots_with_admission_attempts':2,
            'first_attempt_admitted':2,'eventually_admitted':2,'admission_attempt_wall_time':{'n':2,'sum_seconds':1.0}}for m in p['methods']]
        save(self.batch/'round-01/all-method-generation.json',{'rows':rows})
        entry={'id':'charge-one','charged_cny':'.01','status':'usage_reported'}
        save(self.batch/'preparation/round-01/GRAFT/fixture/0/model-cost.json',{'entries':[entry]})
        save(self.batch/'model-cost-ledgers/round-01/GRAFT/fixture.json',{'entries':[entry]})
        fixed.seal_first(self.batch,p)
    def build(self,command,*args):
        out=Path(command[command.index('--outdir')+1]);self.builds.append(out.name)
        (out/'main.pdf').write_bytes(b'Unit-test-only placeholder PDF')
        return {'returncode':0,'timed_out':False}
    def fake_execute(self,task,p,number,dest,*args):
        self.calls.append((number,copy.deepcopy(task),seed_for(p['root_seed'],number,task['crate'],task['slot'])))
        result=dict(method=task['method'],crate=task['crate'],slot=task['slot'],round=number,
            status='measured' if task['status']=='ready' else task['status'],covered_api_ids=['a'] if task['status']=='ready' else [])
        save(dest/'result.json',result);return result
    def run_fixed(self,through=6,resume=False,build=None,executor=None):
        def forbidden(*a,**k):raise AssertionError('No generation or model request in repetitions')
        with patch('run_fixed_generation.execute',side_effect=executor or self.fake_execute),patch('run_experiment_rounds.command_run',side_effect=build or self.build),patch('urllib.request.urlopen',side_effect=forbidden):
            return fixed.run(self.batch,self.paper,through,resume,generate=forbidden)
    def test_all_repetitions_reuse_exact_tasks_keep_failures_and_bill_generation_once(self):
        result=self.run_fixed();self.assertEqual(result['status'],'complete');self.assertEqual(len(self.calls),20)
        for n,t,seed in self.calls:
            self.assertEqual(t,next(x for x in self.tasks if (x['method'],x['slot'])==(t['method'],t['slot'])))
            self.assertEqual(seed,seed_for(17,n,'fixture',t['slot']))
        self.assertEqual(len({seed for n,t,seed in self.calls if t['method']=='GRAFT' and t['slot']==0}),5)
        stats=load(self.paper/'revisions/fixed-unit-fixture/round-06/statistics.json')
        self.assertEqual(stats['independent_generation_rounds'],1);self.assertEqual(stats['independent_afl_seeds_per_generation'],6)
        self.assertEqual(list(stats['generation_by_round']),['1']);self.assertEqual(stats['generation_costs']['paid_or_reserved_calls'],1)
        self.assertEqual(stats['generation_costs']['spent_or_reserved_cny'],'0.01')
        text=(self.paper/'revisions/fixed-unit-fixture/round-06/results.tex').read_text()
        self.assertIn('single generation batch is reused',text);self.assertNotIn('Each round uses an independent generation',text)
        self.assertEqual(load(self.batch/'round-06/summary.json')['rows'][1]['upstream_failure_slots'],1)
        before=len(self.calls);self.run_fixed(resume=True);self.assertEqual(len(self.calls),before)
    def test_publication_failure_blocks_next_round_and_can_resume_without_generation(self):
        def fail(*a):return {'returncode':1,'timed_out':False}
        with self.assertRaisesRegex(RuntimeError,'paper build failed'):self.run_fixed(build=fail)
        self.assertEqual(self.calls,[]);self.assertFalse((self.batch/'round-02').exists())
        self.assertEqual(self.run_fixed(resume=True)['status'],'complete')
    def test_pause_after_publication_and_explicit_round_limit(self):
        self.assertEqual(self.run_fixed(through=1)['status'],'stopped_after_requested_round');self.assertFalse(self.calls)
        def build(command,*a):
            r=self.build(command,*a)
            if self.builds[-1]=='round-02':save(self.batch/'control.json',{'pause':True,'stop_after_round':6,'auto_second':False})
            return r
        self.assertEqual(self.run_fixed(build=build)['status'],'paused_at_round_boundary')
        self.assertFalse((self.batch/'round-03').exists())
    def test_changed_source_extra_seed_or_replay_binary_blocks_reuse(self):
        for kind in ['source','replay_binary','extra_seed']:
            t=self.tasks[0]
            if kind=='extra_seed':p=Path(t['initial_corpus_manifest']['path']).parent/'initial/extra'
            else:p=Path(t[kind]['path'])
            original=p.read_bytes()if p.exists()else None;p.write_bytes(b'changed')
            try:
                with self.assertRaises(ValueError):self.run_fixed()
            finally:
                if original is None:p.unlink()
                else:p.write_bytes(original)
        self.assertFalse(self.calls)
    def test_protocol_cost_or_shared_input_drift_blocks_launch(self):
        f=self.batch/'model-cost-ledgers/round-01/GRAFT/fixture.json';old=f.read_text();f.write_text('{}')
        with self.assertRaises(ValueError):self.run_fixed()
        f.write_text(old)
        save(self.batch/'preparation/round-01/GRAFT/fixture/1/model-cost.json',{'entries':[]})
        with self.assertRaisesRegex(ValueError,'cost ledger'):self.run_fixed()
    def test_cross_round_output_and_changed_slot_manifest_rejected(self):
        manifest=fixed.repetition_manifest(self.batch,self.p,2)
        manifest['tasks'][0]['fuzz_command'][4]='previous/afl'
        with self.assertRaisesRegex(ValueError,'Cross-round'):fixed.verify_tasks(self.p,manifest,2)
        manifest=fixed.repetition_manifest(self.batch,self.p,2);manifest['tasks'].pop()
        with self.assertRaisesRegex(ValueError,'missing'):fixed.verify_tasks(self.p,manifest,2)
    def test_incomplete_repetition_is_unavailable_and_blocks_next(self):
        def fail(t,p,n,dest,*args):
            r=self.fake_execute(t,p,n,dest,*args)
            if t['method']=='GRAFT':r['status']='interrupted'
            return r
        self.assertEqual(self.run_fixed(executor=fail)['status'],'round_incomplete')
        self.assertFalse((self.batch/'round-03').exists())
        summary=load(self.batch/'round-02/summary.json');self.assertIsNone(summary['rows'][0]['coverage_percent'])
        stats=load(self.paper/'revisions/fixed-unit-fixture/round-02/statistics.json');self.assertEqual(stats['independent_afl_seeds_per_generation'],1)
    def test_interrupted_execute_never_restarts_or_overwrites(self):
        t=self.tasks[0];dest=self.batch/'interrupted';save(dest/'started.json',{'started':True})
        first=execute(t,self.p,2,dest,[],self.u);self.assertEqual(first['status'],'interrupted')
        self.assertEqual(execute(t,self.p,2,dest,[],self.u),first)
        t=copy.deepcopy(t);t['version']='2'
        with self.assertRaisesRegex(ValueError,'differs'):execute(t,self.p,2,dest,[],self.u)
    def test_expensive_generation_is_published_but_blocks_repetition(self):
        for f in fixed.cost_files(self.batch):
            save(f,{'entries':[{'id':'charge-one','charged_cny':'.60','status':'usage_reported'}]})
        (self.batch/'generation-seal.json').unlink();fixed.seal_first(self.batch,self.p)
        self.assertEqual(self.run_fixed()['status'],'cost_gate_failed');self.assertFalse(self.calls)
        self.assertTrue((self.batch/'round-01/publication-barrier.json').exists())
    def test_cost_summary_cannot_drift_from_frozen_ledgers(self):
        path=self.batch/'generation-seal.json';data=load(path);data['cost_summary']['spent_or_reserved_cny']='0';save(path,data)
        with self.assertRaisesRegex(ValueError,'cost accounting'):self.run_fixed()
    def test_incomplete_first_generation_still_publishes_unavailable_measurements(self):
        (self.batch/'generation-seal.json').unlink()
        rows=copy.deepcopy(self.first);rows[0]['status']='infrastructure_failed'
        summary=summarize(self.p,rows,{'fixture':self.u},1);save(self.batch/'round-01/summary.json',summary)
        with patch('run_experiment_rounds.command_run',side_effect=self.build):
            result=fixed.run(self.batch,self.paper,generate=lambda *a:summary)
        self.assertEqual(result['status'],'round_incomplete');self.assertFalse((self.batch/'round-02').exists())
        stats=load(self.paper/'revisions/fixed-unit-fixture/round-01/statistics.json')
        self.assertEqual(stats['independent_afl_seeds_per_generation'],0)
        self.assertFalse((self.batch/'round-01/publication-barrier.json').exists())
    def test_first_round_restore_preserves_ready_task_for_reuse(self):
        from launch_controlled_round import Engine
        task=self.tasks[0];path=self.batch/'preparation/round-01/GRAFT/fixture/0/task.json';save(path,task)
        save(self.batch/'round-01/GRAFT/fixture/0/result.json',{'method':'GRAFT','crate':'fixture','slot':0,'status':'measured'})
        e=Engine.__new__(Engine);e.root=self.batch;e.round_name='round-01';e.number=1;e.fixed_generation=True
        e.generation={};e.admissions={};e.results={};e.tasks={};e.identities={};e.fuzz_futures=[]
        Engine.restore_state(e)
        self.assertEqual(e.tasks[('GRAFT','fixture',0)],task)
        self.assertEqual(e.identities[('GRAFT','fixture',0)]['status'],'measured')
    def test_unknown_native_launch_cannot_be_resent_on_resume(self):
        save(self.batch/'deepsurf-native/round-01/process.json',{'status':'starting'})
        with patch('launch_controlled_round.Engine')as engine,patch('run_fixed_generation.subprocess.Popen')as process:
            with self.assertRaisesRegex(ValueError,'PID was sealed'):fixed.first_generation(self.batch,True)
            engine.assert_not_called();process.assert_not_called()
    def test_formal_duration_and_legacy_regeneration_flag_are_enforced(self):
        p=copy.deepcopy(self.p);p['seconds_per_target']=1
        with self.assertRaisesRegex(ValueError,'300 seconds'):fixed.validate_protocol(p)
        save(self.batch/'control.json',{'pause':False,'stop_after_round':6,'auto_second':True})
        with self.assertRaisesRegex(ValueError,'auto_second'):self.run_fixed()
    def test_check_only_does_not_execute_or_publish(self):
        result=fixed.run(self.batch,self.paper,check_only=True)
        self.assertEqual(result['status'],'checked_sealed_generation')
        self.assertFalse((self.paper/'revisions').exists());self.assertFalse((self.batch/'round-02').exists())
    def test_changed_publication_evidence_prevents_next_round(self):
        self.run_fixed(through=1)
        path=self.paper/'revisions/fixed-unit-fixture/round-01/statistics.json';path.write_text('{}')
        with self.assertRaises(ValueError):self.run_fixed()
        self.assertFalse(self.calls)

if __name__=='__main__':unittest.main()
