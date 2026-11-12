#!/usr/bin/env python3
"""Prepare all configurations and stream admitted targets into round-one AFL.

One process owns the ledger. No placeholder failure is used for an unimplemented
adapter. Infrastructure failures keep the round incomplete. Model credentials are
read as data, used only for DeepSeek requests, and removed from execution envs.
"""
import argparse, concurrent.futures as cf, copy, hashlib, json, os, random, re
from pathlib import Path
import shlex, shutil, signal, subprocess, sys, threading, time, urllib.request
import urllib.error

from coverage_snapshot import file_hash, load, measure
from experiment_batch import read_key
from generation_metrics import summarize_generation, summarize_admissions
from model_cost_control import BudgetRejected, compact_controlled, read_policy, read_ledger
from costed_model_request import send as costed_send
from admission_diagnostics import begin_evidence, capture_attempt, repair_feedback
from preflight_admission import read_profile, run_preflight
import parameter_repair
import bounded_replanning
from model_source import extract as extract_model_source
from repair_context import classification, helper_names, documentation_card, task as repair_task
from run_experiment_rounds import command_run, execute, publish, publication_directory, save, summarize, UPSTREAM_FAILURES

ROOT=Path(__file__).resolve().parents[1]
PORT=ROOT.parent.parent/'graft-baseline-port-20261109'
LLVM=Path('/home/cym/.rustup/toolchains/nightly-2026-02-07-x86_64-unknown-linux-gnu/lib/rustlib/x86_64-unknown-linux-gnu/bin')
AFL=Path('/home/cym/.local/share/afl.rs/rustc-1.97.0-nightly-17584a1/afl.rs-0.18.2/afl/bin/afl-fuzz')
PLANNER=Path('/tmp/graft-week1-target/debug/graft-plan')
HARNESS=Path('/tmp/graft-week1-target/debug/graft-harness')
MODEL_MAX_TOKENS=65536
MODEL_TIMEOUT_SECONDS=600

class EmptyModelResponse(RuntimeError):pass
class ModelTimeout(RuntimeError):pass
class ModelBackendError(RuntimeError):pass

class ExistingProcess:
    """Read-only liveness handle for a deepSURF subprocess started before a restart."""
    def __init__(self,pid):self.pid=pid;self.returncode=None
    def poll(self):
        try:
            stat=Path(f'/proc/{self.pid}/stat').read_text().split()
            if len(stat)>2 and stat[2]!='Z' and os.kill(self.pid,0) is None:return None
            self.returncode=-1
        except (OSError,FileNotFoundError):self.returncode=-1
        return self.returncode

def artifact(path):return {'path':str(path.resolve()),'sha256':file_hash(path)}
def clean_env():return {k:v for k,v in os.environ.items() if k not in ('GRAFT_TRACE','GRAFT_PROBE_ARGS') and not any(s in k.upper() for s in ['API_KEY','TOKEN','SECRET','AUTHORIZATION'])}

def compiler_template(log):
    lines=[x for x in Path(log).read_text().splitlines() if 'Running `' in x and '--crate-name graft_joint_compatibility' in x]
    if not lines:raise ValueError('missing root compiler command '+str(log))
    tokens=shlex.split(lines[-1].split('Running `',1)[1].rsplit('`',1)[0])
    index=next(i for i,t in enumerate(tokens) if t.endswith('/rustc'))
    return tokens[index:]

def has_flag_sequence(tokens, flags):
    return any(tokens[i:i+len(flags)]==flags for i in range(len(tokens)-len(flags)+1))

def has_required_compiler_flags(tokens, flags):
    required=[tuple(flags[i:i+2]) for i in range(0,len(flags),2)]
    present={tuple(tokens[i:i+2]) for i in range(len(tokens)-1)}
    return all(item in present for item in required)

class Engine:
    def __init__(self,root,number=1,deep_process=None,resume=False,cost_policy=None,
                 admission_profile=None,planner_bin=None,harness_bin=None,admission_only=False,batch_budget_cny=None,fixed_generation=False,
                 defer_fuzz=False,recovery_skip=None,compile_workers=None,model_workers=None,fuzz_workers=None,preserve_frozen_protocol=False):
        self.fixed_generation=fixed_generation
        self.defer_fuzz=defer_fuzz
        self.recovery_skip=set(tuple(x) for x in (recovery_skip or []))
        if fixed_generation and (number!=1 or admission_only):raise ValueError('fixed generation prepares only the first formal round')
        self.number=number;self.round_name=f'round-{number:02d}';self.root=root;self.runtime=root/'runtime';self.protocol=load(ROOT/'experiments/week5/protocol.json')
        self.deep_process=deep_process
        self.admission_profile=read_profile(admission_profile) if admission_profile else None
        self.parameter_rules=(parameter_repair.read_rules(ROOT/self.admission_profile['semantics_rules'])
                              if self.admission_profile and self.admission_profile.get('semantics_rules') else None)
        self.admission_only=admission_only
        if admission_only and not self.admission_profile:
            raise ValueError('admission-only calibration requires an explicit new profile')
        self.planner_bin=Path(planner_bin) if planner_bin else PLANNER
        self.harness_bin=Path(harness_bin) if harness_bin else HARNESS
        if not self.admission_profile and (planner_bin or harness_bin):
            raise ValueError('binary overrides require a separately frozen admission profile')
        p=self.protocol;p['status']='controlled_short_budget_execution';p['operator_start_policy']='User requested round one, then automatic round two after complete statistics and successful paper update unless new steering arrives; stop after round two.'
        p['generation_adapter_status']='implemented adapters; per-target admission outcomes recorded'
        p['instrumentation_cfg_status']='fuzzing cfg and opt-level=3 matched; same nightly compiler; no sanitizer in coverage track'
        p['coverage_track']='uniform AFL coverage run; no ASan or vulnerability-count claim'
        p['fuzz_workers']=48;p['compile_workers']=8;p['max_workers']=56
        p['model_request_concurrency_allocation']={'controlled_arms':14,'native_deepsurf_crate_workers':2,'total_upper_bound':16}
        p['model_request']={'temperature':0.0,'thinking':{'type':'enabled'},
                            'reasoning_effort':'high','max_tokens':MODEL_MAX_TOKENS,
                            'empty_response_policy':'fail target once; no batch retry'}
        self.cost_policy=read_policy(cost_policy) if cost_policy else None
        self.batch_budget_cny=batch_budget_cny
        self.batch_ledger=root/'batch-model-cost.json' if batch_budget_cny is not None else None
        if batch_budget_cny is not None:
            if not (admission_only and self.cost_policy):raise ValueError('batch budget is currently restricted to admission-only calibration')
            from batch_model_budget import initialize,limit_value
            self.batch_budget_cny=str(limit_value(batch_budget_cny))
            initialize(self.batch_ledger,self.cost_policy,self.batch_budget_cny)
            p['batch_model_budget']={'limit_cny':self.batch_budget_cny,'ledger':str(self.batch_ledger.resolve()),
                                     'implementation':artifact(ROOT/'scripts/batch_model_budget.py')}
        if self.cost_policy:
            if p['model']!=self.cost_policy['model'] or p['model_endpoint']!=self.cost_policy['endpoint']:
                raise ValueError('cost policy model/service differs from the experimental protocol')
            p['model_cost_policy']=self.cost_policy
            p['model_cost_implementation']={name:artifact(ROOT/'scripts'/name) for name in
                ('model_cost_control.py','costed_model_request.py')}
            p['model_request']={'temperature':self.cost_policy['temperature'],
                'thinking':self.cost_policy['thinking'],'max_tokens':self.cost_policy['max_output_tokens'],
                'empty_response_policy':'no retry; retained spend counts against target budget'}
        p['admission_policy']='Default safe calls; permit reviewed u8 alignment and discarded bumpalo raw iterator only. Other direct unsafe without proof is validation_rejected, not a proven invalid harness or library bug.'
        p['failure_policy']='planning_unknown and validation_rejected are actual pipeline admission outcomes with no executed harness. Infrastructure failure remains incomplete.'
        if self.admission_profile:
            p['admission_profile']=self.admission_profile
            p['admission_implementation']={name:artifact(ROOT/'scripts'/name) for name in
                ('admission_diagnostics.py','preflight_admission.py','launch_controlled_round.py',
                 'generation_metrics.py','run_experiment_rounds.py','parameter_repair.py','bounded_replanning.py','model_source.py','repair_context.py')}
            p['admission_binaries']={'planner':artifact(self.planner_bin),
                                    'harness':artifact(self.harness_bin)}
            batch_id=re.sub(r'[^A-Za-z0-9_.-]','_',root.name)+'-'+hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
            p['publication_namespace']='admission-v2/'+batch_id
            p['execution_scope']='admission_only' if admission_only else 'full_fuzz_round'
            if self.parameter_rules:p['parameter_guidance']=artifact(ROOT/self.admission_profile['semantics_rules'])
        if fixed_generation:
            p.update(generation_mode='fixed-generation-six-fuzz-v1', generation_repetitions=1,
                     max_repairs_after_initial=self.cost_policy['max_calls_per_target']-1 if self.cost_policy else p['max_repairs_after_initial'],
                     fuzz_repetitions=6,afl_seeds_per_generation=6,
                     repetition_policy='Generate once; reuse identical sealed tasks and initial corpus for six paired independent AFL seeds.',
                     operator_start_policy='Continue through the configured round after successful publication unless paused; never use legacy auto_second.')
            p['fixed_generation_implementation']=artifact(ROOT/'scripts/run_fixed_generation.py')
        p['universes']={c:artifact(self.runtime/'universes/api_universes'/(c+'.json')) for c in p['versions']}
        p['definition_count']=sum(sum(a.get('public_name_body_definition',False) for a in load(v['path'])['apis']) for v in p['universes'].values())
        p['cfg_flags']=['--cfg','fuzzing','-C','opt-level=3','-C','codegen-units=1','-C','target-cpu=native']

        self.templates={'afl':compiler_template(self.runtime/'afl-build.log'),'coverage':compiler_template(self.runtime/'coverage-matched-build.log')}
        if any(not has_required_compiler_flags(t,p['cfg_flags']) for t in self.templates.values()):
            raise ValueError('runtime compiler templates do not contain the frozen cfg flags')
        compiler_paths={str(Path(t[0]).resolve()) for t in self.templates.values()}
        if len(compiler_paths)!=1:
            raise ValueError('AFL and coverage templates use different compilers')
        shared=PORT/'joint-compatibility-v2'
        p['runtime_evidence']={
            'shared_project':str(shared.resolve()),
            'shared_manifest':{'path':str((shared/'Cargo.toml').resolve()),'sha256':file_hash(shared/'Cargo.toml')},
            'shared_lock':{'path':str((shared/'Cargo.lock').resolve()),'sha256':file_hash(shared/'Cargo.lock')},
            'compiler':artifact(Path(next(iter(compiler_paths)))),
            'compiler_templates':{k:{'path':str((self.runtime/f'{k}-build.log').resolve()),
                                      'sha256':file_hash(self.runtime/f'{k}-build.log'),
                                      'flags_verified':has_required_compiler_flags(v,p['cfg_flags'])}
                                  for k,v in self.templates.items()},
            'global_data_build':artifact(self.runtime/'global-data-build.json'),
        }
        if p['runtime_evidence']['shared_lock']['sha256']!=p['shared_lock']['sha256']:
            raise ValueError('runtime Cargo.lock differs from frozen shared lock')
        if p['runtime_evidence']['shared_manifest']['sha256']!=p['shared_manifest']['sha256']:
            raise ValueError('runtime Cargo.toml differs from frozen shared manifest')
        if (root/'protocol.json').exists():
            frozen=load(root/'protocol.json');frozen.pop('_file_sha256',None)
            if preserve_frozen_protocol:
                p=frozen;self.protocol=p
            elif frozen!=p:raise ValueError('frozen protocol changed; use a new batch')
        else:save(root/'protocol.json',p)
        p['_file_sha256']=file_hash(root/'protocol.json')
        self.env=clean_env()
        self.compile_workers=compile_workers or p['compile_workers']
        self.model_workers=model_workers or p['model_request_concurrency_allocation']['controlled_arms']
        self.fuzz_workers=fuzz_workers or p['fuzz_workers']
        self.compile_slots=threading.Semaphore(self.compile_workers)
        self.model_slots=threading.Semaphore(self.model_workers)
        self.compile_address_space_bytes=4*1024**3
        self.lock=threading.RLock();self.worker_local=threading.local();self.available_cpus=sorted(os.sched_getaffinity(0))[:48]
        def assign_cpu():
            with self.lock:self.worker_local.cpu=self.available_cpus.pop()
        self.fuzz_pool=cf.ThreadPoolExecutor(max_workers=1 if defer_fuzz else min(48,self.fuzz_workers),initializer=assign_cpu)
        self.fuzz_futures=[];self.results={};self.tasks={};self.generation={};self.admissions={};self.generator_runs={};self.source_index={};self.universes={c:load(v['path']) for c,v in p['universes'].items()}
        self.started=time.time();self.requests=0;self.last_metrics_write=0
        self.initial=root/'initial';self.initial.mkdir(exist_ok=True)
        samples=[bytes(n) for n in [1,8,32,64,128,256,1024]]+[bytes([1])*64,b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/']
        for i,b in enumerate(samples):(self.initial/str(i)).write_bytes(b)
        self.identities={(m,c,i):{'method':m,'crate':c,'version':v,'slot':i,'status':'pending'} for m in p['methods'] for c,v in p['versions'].items() for i in range(20)}
        if resume:self.restore_state()
        self.update()
    def restore_state(self):
        """Rebuild the in-memory ledger from sealed preparation and result files."""
        base=self.root/'preparation'/self.round_name
        restored=[]
        for generation_path in sorted(base.glob('*/*/*/generation.json')):
            record=load(generation_path)
            key=(record['method'],record['crate'],record['slot'])
            self.generation[key]=record
        for admission_path in sorted(base.glob('*/*/*/admission-*.json')):
            parts=admission_path.parts
            key=(parts[-4],parts[-3],int(parts[-2]))
            self.admissions.setdefault(key,[]).append(load(admission_path))
        for task_path in sorted(base.glob('*/*/*/task.json')):
            task=load(task_path);key=(task['method'],task['crate'],task['slot'])
            result_path=self.root/self.round_name/task['method']/task['crate']/str(task['slot'])/'result.json'
            if result_path.exists():
                result=load(result_path);self.results[key]=result;task=dict(task,status=result['status'])
            elif task['status']=='ready':
                restored.append(task)
            if self.fixed_generation and (task_path.parent/'task.json').exists():task=load(task_path)
            self.tasks[key]=task;self.identities[key]=dict(task,status=self.results[key]['status']) if key in self.results else task
        for result_path in sorted((self.root/self.round_name).glob('*/*/*/result.json')):
            result=load(result_path);key=(result['method'],result['crate'],result['slot'])
            self.results[key]=result
            if key in self.tasks:self.identities[key]=dict(self.tasks[key],status=result['status'])
        for task in restored:
            if self.defer_fuzz:continue
            if (self.root/self.round_name/task['method']/task['crate']/str(task['slot'])/'result.json').exists():
                continue
            self.fuzz_futures.append(self.fuzz_pool.submit(self.fuzz,task))
        save(self.root/'resume-ledger.json',{'round':self.number,'restored_tasks':len(self.tasks),
            'restored_results':len(self.results),'restored_generations':len(self.generation),
            'restored_admission_records':sum(map(len,self.admissions.values()))})
    def update(self):
        with self.lock:
            counts={};crates={}
            for k,t in self.identities.items():
                s=t['status'];counts[s]=counts.get(s,0)+1
                row=crates.setdefault(k[1],{});row[s]=row.get(s,0)+1
            save(self.root/'progress.json',{'status':'running','round':self.number,'elapsed_seconds':time.time()-self.started,'target_states':counts,'by_crate':crates,'model_requests':self.requests,'fuzz_jobs_submitted':len(self.fuzz_futures),'formal_round_complete':False,'second_round_auto_start':not self.fixed_generation})
            if time.monotonic()-self.last_metrics_write<2:return
            self.last_metrics_write=time.monotonic()
            metrics=summarize_generation([copy.deepcopy(v) for k,v in self.generation.items() if k[0]=='GRAFT'],list(self.protocol['versions']))
            metrics.update(round=self.number,experiment_elapsed_seconds=time.time()-self.started,
                           planning_outcomes={c:{s:sum(1 for (m,crate,i),v in self.identities.items() if m=='GRAFT' and crate==c and v['status']==s) for s in ['pending','planning_unknown','infrastructure_failed']} for c in self.protocol['versions']})
            save(self.root/self.round_name/'graft-generation.json',metrics)
            save(self.root/'graft-generation-latest.json',metrics)
            p=self.protocol
            all_metrics=summarize_admissions(p['methods'],list(p['versions']),self.identities,self.admissions,self.generator_runs)
            all_metrics.update(round=self.number,experiment_elapsed_seconds=time.time()-self.started)
            all_metrics['llm_generation']={m:summarize_generation([copy.deepcopy(v) for k,v in self.generation.items() if k[0]==m],list(p['versions'])) for m in p['methods'] if any(k[0]==m for k in self.generation)}
            save(self.root/self.round_name/'all-method-generation.json',all_metrics)
            save(self.root/'all-method-generation-latest.json',all_metrics)
    def directory(self,m,c,i):
        path=self.root/'preparation'/self.round_name/m/c/str(i);path.mkdir(parents=True,exist_ok=True);return path
    def runtime_checks(self,source):
        p=self.protocol;ev=p['runtime_evidence']
        lock_ok=file_hash(ev['shared_lock']['path'])==ev['shared_lock']['sha256']==p['shared_lock']['sha256']
        manifest_ok=(file_hash(ev['shared_manifest']['path'])==ev['shared_manifest']['sha256']
                     ==p['shared_manifest']['sha256'])
        flags_ok=all(has_required_compiler_flags(t,p['cfg_flags']) for t in self.templates.values())
        global_data_used='global_data' in source.read_text(errors='replace')
        global_data={}
        if global_data_used:
            rows=load(ev['global_data_build']['path'])
            by_mode={r['mode']:r for r in rows}
            for mode in ('afl','coverage'):
                rlib=file_hash(self.runtime/f'libglobal_data-{mode}.rlib')
                global_data[mode]={'recorded':by_mode.get(mode,{}),
                    'rlib_sha256':rlib,'verified':rlib==by_mode.get(mode,{}).get('binary_sha256')}
            if not all(v['verified'] for v in global_data.values()):
                raise ValueError('global_data support-library evidence is missing or changed')
        if not lock_ok or not manifest_ok or not flags_ok:
            raise ValueError('shared lock/manifest/compiler-template evidence mismatch')
        return {'shared_lock_verified':lock_ok,'shared_manifest_verified':manifest_ok,
                'instrumentation_cfg_verified':flags_ok,
                'compiler':ev['compiler'],'templates':ev['compiler_templates'],
                'global_data_used':global_data_used,'global_data':global_data,
                'scope':'versions and features are bounded by the frozen shared manifest/lock; '
                        'global_data rlibs must match their reviewed build record'}
    def failure(self,m,c,i,status,reason,evidence=None):
        d=self.directory(m,c,i)
        record={'round':self.number,'method':m,'crate':c,'slot':i,'status':status,'reason':reason,'detail':evidence}
        save(d/'outcome.json',record)
        task={**self.identities[(m,c,i)],'status':status,'evidence':artifact(d/'outcome.json')}
        with self.lock:
            self.tasks[(m,c,i)]=task;self.identities[(m,c,i)]=task
            self.results[(m,c,i)]=record;self.update()
    def compile(self,source,output,mode,timeout=90,include_queue_wait=False):
        command=[];template=self.templates[mode];i=0
        while i<len(template):
            t=template[i]
            if t=='--crate-name':command += [t,'graft_'+hashlib.sha256(str(source).encode()).hexdigest()[:12]];i+=2;continue
            if t=='src/main.rs':command.append(str(source));i+=1;continue
            if t=='--out-dir':i+=2;continue
            if t=='-C' and template[i+1].split('=')[0] in ['metadata','extra-filename','incremental']:i+=2;continue
            if t.startswith('--emit='):t='--emit=link'
            command.append(t);i+=1
        command += ['-o',str(output)]
        if 'global_data' in source.read_text():
            support=self.runtime/('libglobal_data-'+mode+'.rlib')
            if not support.exists():raise RuntimeError('reviewed native global-data support missing')
            command+=['--extern','global_data='+str(support)]
        wait_start=time.monotonic()
        acquired=self.compile_slots.acquire(timeout=timeout if include_queue_wait else None)
        if not acquired:raise TimeoutError('preflight compile queue budget exhausted')
        try:
            waited=time.monotonic()-wait_start
            remaining=timeout-waited if include_queue_wait else timeout
            if remaining<=0:raise TimeoutError('preflight compile queue budget exhausted')
            guarded=['prlimit','--as='+str(self.compile_address_space_bytes),'--']+command
            result=command_run(guarded,self.runtime/'project',self.env,output.with_suffix('.compile.log'),remaining,sorted(os.sched_getaffinity(0))[48:56])
        finally:self.compile_slots.release()
        result['waiting_compile_seconds']=waited
        if not hasattr(self.worker_local,'compile_runs'):self.worker_local.compile_runs=[]
        self.worker_local.compile_runs.append(dict(mode=mode,output=str(output),**result))
        return result
    def normalize(self,source,d,plan=None,baseline=False):
        command=[str(self.harness_bin),str(source),str(d/'normalized.rs'),str(d/'validity.json')]
        if plan:
            command += (['--repair-plan',str(plan),'--standalone-output',str(d/'standalone.rs')]
                        if parameter_repair.enabled(self.admission_profile) else ['--plan',str(plan)])
            if parameter_repair.extended(self.admission_profile):command += ['--observe-objects']
        if baseline:command += ['--baseline']
        required=d/'required-names.json'
        if required.exists():command += ['--required-names',str(required)]
        result=command_run(command,d,self.env,d/'normalize.log',15)
        if result['returncode']!=0:return False,result
        return True,load(d/'validity.json')
    def admit(self,m,c,i,source,plan=None,baseline=False):
        d=self.directory(m,c,i)
        if (d/'task.json').exists():
            return load(d/'task.json')['status'] not in ('generation_failed','generation_timeout',
                'planning_unknown','infrastructure_failed','validation_rejected','compile_failed','no_target')
        start=time.monotonic();admitted=False
        if self.admission_profile and (m,c,i) not in self.generation:begin_evidence(d)
        compile_before=len(getattr(self.worker_local,'compile_runs',[]))
        admission={'source':artifact(source),'started_unix_seconds':time.time(),'accepted':False}
        with self.lock:self.admissions.setdefault((m,c,i),[]).append(admission)
        try:
            runtime_checks=self.runtime_checks(source)
            repair_mode=bool(plan and parameter_repair.enabled(self.admission_profile))
            if repair_mode:
                ok,review=self.normalize(source,d,plan,baseline)
                if not ok:self.failure(m,c,i,'validation_rejected','parameter repair contract rejected',review);return False
            if plan:
                original=self.compile(d/'standalone.rs' if repair_mode else source,d/'original-wrapper','coverage')
                if original['returncode']!=0:self.failure(m,c,i,'compile_failed','original planned wrapper did not compile',original);return False
                extra=['--extended'] if parameter_repair.extended(self.admission_profile) else []
                original_probe=command_run(['prlimit','--as=1073741824','--',str(self.planner_bin),'probe',str(plan),str(d/'original-wrapper'),str(d/'original-wrapper-probe.json')]+extra,d,dict(self.env,LLVM_PROFILE_FILE='/dev/null'),d/'original-wrapper-probe.log',20)
                if original_probe['returncode']!=0:self.failure(m,c,i,'validation_rejected','original planned wrapper failed three-input probe',original_probe);return False
            if not repair_mode:
                ok,review=self.normalize(source,d,plan,baseline)
                if not ok:self.failure(m,c,i,'validation_rejected','normalization/contract admission failed',review);return
            compiled=self.compile(d/'normalized.rs',d/'replay','coverage')
            if compiled['returncode']!=0:self.failure(m,c,i,'compile_failed','common coverage compiler rejected target',compiled);return
            if plan:
                probe_env=dict(self.env,GRAFT_PROBE_ARGS='1',LLVM_PROFILE_FILE='/dev/null')
                probe=command_run(['prlimit','--as=1073741824','--',str(self.planner_bin),'probe',str(plan),str(d/'replay'),str(d/'wrapper-probe.json')]+extra,d,probe_env,d/'probe.log',20)
                if probe['returncode']!=0:self.failure(m,c,i,'validation_rejected','planned-call/three-input wrapper probe failed',probe);return
            wrapper_probe_passed=True
            valid=d/'initial';valid.mkdir(exist_ok=True);probes=[];profiles=[]
            for seed in sorted(self.initial.iterdir()):
                env=dict(self.env,LLVM_PROFILE_FILE=str(d/('probe-'+seed.name+'-%p.profraw')))
                run=command_run(['prlimit','--as=1073741824','--',str(d/'replay'),str(seed)],d,env,d/('input-'+seed.name+'.log'),3)
                probes.append({'input':artifact(seed),'run':run})
                if run['returncode']==0 and not run['timed_out']:
                    shutil.copyfile(seed,valid/seed.name);profiles+=list(d.glob('probe-'+seed.name+'-*.profraw'))
            if not profiles:self.failure(m,c,i,'validation_rejected','no successful instrumented initial input',probes);return
            merge=command_run([str(LLVM/'llvm-profdata'),'merge','-sparse',*map(str,profiles),'-o',str(d/'probe.profdata')],d,self.env,d/'merge.log',30)
            if merge['returncode']!=0:raise RuntimeError('LLVM profile version incompatibility')
            with (d/'probe-coverage.json').open('wb') as out,(d/'probe-export.log').open('wb') as err:
                exp=subprocess.run([str(LLVM/'llvm-cov'),'export',str(d/'replay'),'-instr-profile='+str(d/'probe.profdata')],stdout=out,stderr=err,timeout=30)
            if exp.returncode!=0:raise RuntimeError('LLVM export failed')
            coverage=measure(self.universes[c],load(d/'probe-coverage.json'))
            entry=coverage['groups']['public_name_body_definitions']['covered']
            if entry==0 and not plan:self.failure(m,c,i,'validation_rejected','no public library entry confirmed on initial input probes',probes);return
            compiled_afl=self.compile(d/'normalized.rs',d/'fuzz','afl')
            if compiled_afl['returncode']!=0:self.failure(m,c,i,'compile_failed','common AFL compiler rejected target',compiled_afl);return
            save(d/'input-probes.json',{'probes':probes,'public_entries':entry,'scope':'bounded arrival and AST byte-use checks, not complete taint proof'})
            gates={'unsafe_contracts_reviewed':review.get('accepted') is True,
                'wrapper_probe_passed':wrapper_probe_passed,
                'shared_lock_features_verified':runtime_checks['shared_lock_verified'] and runtime_checks['shared_manifest_verified'],
                'instrumentation_cfg_verified':runtime_checks['instrumentation_cfg_verified']}
            if not all(gates.values()):raise ValueError('admission evidence gate failed: '+json.dumps(gates))
            save(d/'build-evidence.json',{'coverage':compiled,'afl':compiled_afl,
                'shared_lock':self.protocol['shared_lock'],'shared_manifest':self.protocol['shared_manifest'],
                'cfg':self.protocol['cfg_flags'],'runtime_checks':runtime_checks,'gates':gates,
                'normalization_review':review,'sanitizer':'none, uniform coverage track'})
            save(d/'initial-manifest.json',{'files':[artifact(p) for p in sorted(valid.iterdir())],'policy':'same base seed pool; failed initial inputs logged and excluded before fuzzing, without coverage-guided selection'})
            task={**self.identities[(m,c,i)],'status':'ready','workers':1,'binary':artifact(d/'fuzz'),'source':artifact(d/'normalized.rs'),'validity_evidence':artifact(d/'validity.json'),'build_evidence':artifact(d/'build-evidence.json'),'initial_corpus_manifest':artifact(d/'initial-manifest.json'),**gates,
                'fuzz_command':[str(AFL),'-V','{seconds}','-s','{seed}','-i',str(valid),'-o','{run_dir}/afl','-m','1024','-t','1000','--',str(d/'fuzz')],
                'replay_command':[sys.executable,str(ROOT/'scripts/replay_round_coverage.py'),'--initial',str(valid),'--afl-output','{run_dir}/afl','--binary',str(d/'replay'),'--llvm-bin',str(LLVM),'--output','{coverage_export}','--work','{run_dir}/replayed'],
                'environment':{'AFL_NO_UI':'1','AFL_NO_AFFINITY':'1','AFL_SKIP_CPUFREQ':'1','AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES':'1','LLVM_PROFILE_FILE':'/dev/null'}}
            if self.fixed_generation:
                task['replay_binary']=artifact(d/'replay')
                task['replay_adapter']=artifact(ROOT/'scripts/replay_round_coverage.py')
            save(d/'task.json',task)
            with self.lock:
                self.tasks[(m,c,i)]=task
                self.results.pop((m,c,i),None)
                if getattr(self,'admission_only',False) or self.defer_fuzz:
                    self.identities[(m,c,i)]=dict(task,status='admitted_not_fuzzed')
                else:
                    self.identities[(m,c,i)]=dict(task,status='queued')
                    future=self.fuzz_pool.submit(self.fuzz,task);self.fuzz_futures.append(future)
                self.update()
            admitted=True
            return True
        except Exception as error:self.failure(m,c,i,'infrastructure_failed',str(error))
        finally:
            admission.update(accepted=admitted,elapsed_seconds=time.monotonic()-start,
                             compiler_runs=getattr(self.worker_local,'compile_runs',[])[compile_before:])
            save(d/('admission-'+str(len(self.admissions[(m,c,i)])-1)+'.json'),admission)
            if self.admission_profile and (m,c,i) not in self.generation:
                capture_attempt(d,len(self.admissions[(m,c,i)])-1,source,
                                admitted,{'method':m,'crate':c,'slot':i})
            with self.lock:self.update()
    def preflight(self,plan,d,wall_seconds=None):
        """Local core checks precede paid generation under the opt-in profile."""
        def emit_core(plan,directory,timeout):
            result=command_run([str(self.planner_bin),'emit',str(plan),str(directory/'emitted')],
                               directory,self.env,directory/'emit.log',timeout)
            if result['returncode']!=0:raise RuntimeError('deterministic core emission failed')
            return directory/'emitted/src/main.rs'
        def compile_core(source,binary,timeout):
            self.runtime_checks(source)
            return self.compile(source,binary,'coverage',timeout=timeout,include_queue_wait=True)
        def probe_core(plan,binary,report,timeout):
            return command_run(['prlimit','--as=1073741824','--',str(self.planner_bin),
                                'probe',str(plan),str(binary),str(report)],report.parent,
                               dict(self.env,LLVM_PROFILE_FILE='/dev/null'),
                               report.with_suffix('.log'),timeout)
        return run_preflight(plan,d/'core-preflight',emit_core,compile_core,probe_core,
            {'protocol_sha256':self.protocol['_file_sha256']},
            min(self.admission_profile['preflight_wall_seconds'],wall_seconds)
            if wall_seconds is not None else self.admission_profile['preflight_wall_seconds'])
    def replan(self,initial_plan,d,method,crate):
        bounds=self.admission_profile.get('replanning')
        if not isinstance(bounds,dict):return None
        initial=load(initial_plan)['plan']
        catalog=self.root/'plans'/self.round_name/crate/'catalog.json'
        def generate(report,timeout,max_alternatives):
            began=time.monotonic()
            selected_catalog=catalog
            if not selected_catalog.exists():
                selected_catalog=report.parent/'catalog.json'
                raw=self.runtime/'raw-rustdoc'/(crate+'-'+initial['crate_version']+'.json')
                command=[str(self.planner_bin),'import',str(raw),str(selected_catalog),'--package',initial['package_name']]
                for feature in initial['features']:command+=['--features',feature]
                if not initial['default_features']:command+=['--no-default-features']
                result=command_run(command,d,self.env,report.parent/'import.log',min(timeout,15))
                if result['returncode']!=0:raise RuntimeError('replanning catalog import failed')
                cat=load(selected_catalog);allowed={a['rustdoc_id'] for a in self.universes[crate]['apis'] if a.get('public_name_body_definition')}
                cat['apis']=[a for a in cat['apis'] if a['id'] in allowed];save(selected_catalog,cat)
            remaining=timeout-(time.monotonic()-began)
            if remaining<=0:raise TimeoutError('replanning catalog exhausted wall budget')
            command=[str(self.planner_bin),'alternatives',str(initial_plan),str(selected_catalog),str(report),
                     '--max-candidates',str(max_alternatives),'--nodes',str(bounds['search_nodes'])]
            if method=='syntactic+LLM':command+=['--syntactic']
            result=command_run(command,d,self.env,report.parent/'generate.log',remaining)
            if result['timed_out']:raise TimeoutError('alternate planning exhausted wall budget')
            if result['returncode']!=0:raise RuntimeError('alternate planner failed')
            return load(report)
        chosen=bounded_replanning.choose(initial_plan,d/'replanning',bounds,generate,
            lambda plan,work,timeout:self.preflight(plan,work,wall_seconds=timeout))
        if load(d/'replanning/plan-lineage.json').get('status')=='infrastructure_failed':
            raise ModelBackendError('bounded replanning infrastructure failed; original target budget retained')
        return chosen
    def parameter_prompt(self,plan,prompt):
        """Retrieve documentation for the active plan, including bounded helpers."""
        if self.admission_profile.get('repair_tasks'):
            p=load(plan)['plan'];c=p['crate_name']
            directory=self.root/'repair-context-catalog';directory.mkdir(exist_ok=True)
            catalog=directory/(c+'.json')
            if not catalog.exists():
                cmd=[str(self.planner_bin),'import',str(self.runtime/'raw-rustdoc'/(c+'-'+p['crate_version']+'.json')),
                     str(catalog),'--package',p['package_name']]
                for feature in p['features']:cmd+=['--features',feature]
                if not p['default_features']:cmd+=['--no-default-features']
                result=command_run(cmd,directory,self.env,catalog.with_suffix('.log'),15)
                if result['returncode']!=0:raise ModelBackendError('active documentation import failed')
            cat=load(catalog)
            if cat['rustdoc_sha256']!=p['rustdoc_sha256']:raise ModelBackendError('active documentation rustdoc changed')
            raw=load(self.runtime/'raw-rustdoc'/(c+'-'+p['crate_version']+'.json'))
            ids={s['api'] for s in p['steps'] if s['kind']=='call'}
            allowed={a['rustdoc_id'] for a in self.universes[c]['apis'] if a.get('public_name_body_definition')}
            helpers=helper_names(p)
            docs=[documentation_card(api,raw['index'].get(api['id'],{}).get('docs',''),
                   0 if api['id']==p['target'] else (1 if api['path'] in helpers else 2))
                  for api in cat['apis'] if api['id'] in ids or (api['id'] in allowed and api['path'] in helpers)]
            prefix=prompt.split('Public documentation pool:\n',1)[0]
            prompt=prefix+'Public documentation pool:\n'+json.dumps(docs,ensure_ascii=False)+'\nRender the following pinned SequencePlan'
        return parameter_repair.base_prompt(plan,prompt,getattr(self,'parameter_rules',None))
    def fuzz(self,task):
        m,c,i=task['method'],task['crate'],task['slot'];key=(m,c,i)
        with self.lock:self.identities[key]=dict(task,status='fuzzing');self.update()
        # Multiple targets share a logical process slot only through the fixed
        # pool, without giving a method its own enlarged worker budget.
        result=execute(task,self.protocol,self.number,self.root/self.round_name/m/c/str(i),[self.worker_local.cpu],self.universes[c])
        with self.lock:self.results[key]=result;self.identities[key]=dict(task,status=result['status']);self.update()
        return result
    def request(self,prompt,d,index):
        if self.admission_profile and any((d/name).exists() for name in
            (f'request-{index}.json',f'raw-response-{index}.json',f'response-{index}.json')):
            raise ModelBackendError('request artifacts already exist; reconcile the interrupted '
                                    'attempt instead of overwriting or paying for a resend')
        key=read_key(Path.home()/'.config/go-exit/deepseek.env')
        request_config=self.protocol['model_request']
        body={'model':self.protocol['model'],'messages':[{'role':'user','content':prompt}],
              'temperature':request_config['temperature'],'stream':False,
              'max_tokens':request_config['max_tokens'],'thinking':request_config['thinking']}
        if 'reasoning_effort' in request_config:body['reasoning_effort']=request_config['reasoning_effort']
        if self.cost_policy:
            prompt=compact_controlled(prompt,self.cost_policy)
            body['messages']=[{'role':'user','content':prompt}]
        save(d/f'request-{index}.json',body)
        (d/f'attempt-{index}.prompt.txt').write_text(prompt)
        req=urllib.request.Request(self.protocol['model_endpoint'],data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
        start=time.monotonic()
        def count_actual_request():
            with self.lock:self.requests+=1;self.update()
        try:
            with self.model_slots:
                if self.cost_policy:
                    raw=costed_send(body['messages'],self.cost_policy,d/'model-cost.json',
                        self.root/'model-cost-ledgers'/self.round_name/d.parts[-3]/(d.parts[-2]+'.json'),
                        d/f'request-{index}.json',d/f'raw-response-{index}.json',credential=key,on_send=count_actual_request,
                        batch_ledger=getattr(self,'batch_ledger',None),batch_budget_cny=getattr(self,'batch_budget_cny',None),
                        target_identity='/'.join(d.parts[-3:]))
                else:
                    count_actual_request()
                    with urllib.request.urlopen(req,timeout=MODEL_TIMEOUT_SECONDS) as response:raw=json.load(response)
        except (TimeoutError,):
            raise ModelTimeout('model request exceeded '+str(MODEL_TIMEOUT_SECONDS)+' seconds')
        except urllib.error.HTTPError as error:
            error_body=error.read().decode(errors='replace')[:20000]
            save(d/f'response-{index}.json',{'latency_seconds':time.monotonic()-start,
                'http_status':error.code,'error_body':error_body})
            raise ModelBackendError('DeepSeek HTTP '+str(error.code))
        except urllib.error.URLError as error:
            save(d/f'response-{index}.json',{'latency_seconds':time.monotonic()-start,
                'transport_error':type(error.reason).__name__ if hasattr(error,'reason') else type(error).__name__})
            raise ModelBackendError('DeepSeek transport error: '+type(error).__name__)
        latency=time.monotonic()-start
        save(d/f'response-{index}.json',{'latency_seconds':latency,'response':raw})
        choices=raw.get('choices') or []
        if not choices:
            raise EmptyModelResponse('response has no choices')
        choice=choices[0];message=choice.get('message') or {}
        text=(message.get('content') or '').strip();finish=choice.get('finish_reason')
        metadata={'finish_reason':finish,'content_length':len(text),
            'reasoning_content_length':len(message.get('reasoning_content') or ''),
            'usage':raw.get('usage',{}),'response_id':raw.get('id')}
        if self.cost_policy:
            entry=read_ledger(d/'model-cost.json')['entries'][-1]
            metadata.update(peak_estimate_cny=entry['charged_cny'],cost_policy_revision=self.cost_policy['revision'])
        if not text:
            raise EmptyModelResponse(json.dumps(metadata,sort_keys=True))
        if self.cost_policy and finish!='stop':raise EmptyModelResponse('truncated output; no automatic paid retry: '+json.dumps(metadata,sort_keys=True))
        processing_start=time.monotonic()
        raw_source=d/f'attempt-{index}.raw.txt';raw_source.write_text(text)
        plain_verified=False
        if self.admission_profile and self.admission_profile.get('repair_tasks'):
            parsed=command_run([str(self.planner_bin),'source-key',str(raw_source),str(d/f'attempt-{index}.raw-key.json')],
                               d,self.env,d/f'attempt-{index}.raw-key.log',5)
            plain_verified=parsed['returncode']==0
        try:text,extraction=extract_model_source(text,validated_plain=plain_verified)
        except ValueError as error:
            save(d/f'attempt-{index}.extraction.json',{'accepted':False,'reason':str(error)})
            # Preserve rejected bytes for the normal Rust gate and repair feedback.
        else:
            save(d/f'attempt-{index}.extraction.json',dict(extraction,accepted=True))
            metadata['source_extraction_kind']=extraction['kind']
        metadata['source_processing_seconds']=time.monotonic()-processing_start
        path=d/f'attempt-{index}.rs';path.write_text(text);return path,metadata
    def model(self,m,c,i,prompt,plan=None,paired=False):
        d=self.directory(m,c,i);original=prompt;last=None
        initial_plan=plan
        began=time.monotonic();record=copy.deepcopy(self.generation[(m,c,i)])
        if 'started_unix_seconds' not in record:
            record.update(started_unix_seconds=time.time(),
                          queue_wait_seconds=time.time()-record['queued_unix_seconds'])
        first_index=len(record.get('attempts',[]))
        max_calls=self.cost_policy['max_calls_per_target'] if self.cost_policy else 4
        if first_index>=max_calls:
            self.failure(m,c,i,'generation_failed','maximum generation attempts reached without admission',record);return
        base_prompt=prompt
        repair_mode=bool(plan and parameter_repair.enabled(self.admission_profile))
        record['max_model_calls']=max_calls
        pending_validation=None
        if first_index:
            saved=d/f'attempt-{first_index-1}.prompt.txt'
            if saved.exists():original=saved.read_text()
            lineage=d/'replanning/plan-lineage.json'
            if lineage.exists() and load(lineage).get('selected'):
                selected=load(lineage)['selected']
                if artifact(Path(selected['path']))!=selected:raise ValueError('resumed alternate plan changed')
                plan=Path(selected['path'])
        try:
            if self.admission_profile and plan:
                preflight=self.preflight(plan,d);record['core_preflight']=preflight
                if preflight['status']!='passed' and not (repair_mode and preflight['status']=='rejected'):
                    status='planning_unknown' if preflight['status']=='rejected' else 'infrastructure_failed'
                    record['status']=status
                    self.failure(m,c,i,status,'core preflight blocked paid generation',preflight)
                    if paired:self.failure('no-repair',c,i,status,'shared core preflight blocked generation',preflight)
                    return
                if repair_mode:
                    seed=d/'parameter-repair-seed.rs'
                    emitted=command_run([str(self.planner_bin),'repair-seed',str(plan),str(seed)],
                                        d,self.env,d/'repair-seed.log',15)
                    if emitted['returncode']!=0:raise RuntimeError('parameter repair seed generation failed')
                    base_prompt=self.parameter_prompt(plan,prompt)
                    original=parameter_repair.prompt(base_prompt,seed.read_text(),preflight['diagnosis'],plan)
                    if first_index:
                        previous=d/f'attempt-{first_index-1}.rs'
                        diagnosis=record['attempts'][-1].get('validation_evidence',{}).get('diagnosis')
                        if not previous.exists() or not diagnosis:
                            raise ModelBackendError('interrupted parameter repair lacks sealed '
                                                    'source/diagnosis; reconcile before another paid call')
                        prior_plan=record['attempts'][-1].get('plan')
                        original=parameter_repair.prompt(base_prompt,
                            seed.read_text() if prior_plan and prior_plan!=artifact(plan) else previous.read_text(),diagnosis,plan)
                    record.update(strategy='llm_parameter_repair',
                                  preflight_action='llm_repair' if preflight['status']=='rejected' else 'llm_generation')
                    if self.admission_profile.get('repair_tasks'):
                        record['input_applicability']=classification(load(plan)['plan'])
                    save(d/'parameter-repair-context.json',{'plan':artifact(plan),'seed':artifact(seed),
                        'preflight_diagnosis':preflight['diagnosis'],'target_identity':{'method':m,'crate':c,'slot':i},
                        'parameter_rules':parameter_repair.rule_context(load(plan)['plan'],getattr(self,'parameter_rules',None))})
            for attempt in range(first_index,max_calls):
                compile_before=len(getattr(self.worker_local,'compile_runs',[]))
                attempt_record={'index':attempt,'kind':'initial' if attempt==0 else 'repair',
                    'response_received':False,'accepted':False}
                if plan:attempt_record['plan']=artifact(plan)
                record['attempts'].append(attempt_record)
                model_start=time.monotonic()
                source,response_meta=self.request(original,d,attempt);last=source
                if self.admission_profile and self.admission_profile.get('repair_tasks'):
                    key_started=time.monotonic()
                    key_path=d/f'attempt-{attempt}.source-key.json'
                    key_run=command_run([str(self.planner_bin),'source-key',str(source),str(key_path)],d,self.env,
                                        d/f'attempt-{attempt}.source-key.log',5)
                    if key_run['returncode']==0 and key_path.exists():
                        attempt_record['source_token_key']=load(key_path)['sha256']
                        attempt_record['paid_response_same_ast']=any(a.get('source_token_key')==attempt_record['source_token_key']
                            for a in record['attempts'][:-1])
                    attempt_record['source_key_seconds']=time.monotonic()-key_started
                response_record=load(d/f'response-{attempt}.json')
                attempt_record.update(response_received=True,model_seconds=response_record['latency_seconds'],
                                      model_service_wait_and_io_seconds=max(0,time.monotonic()-model_start-response_record['latency_seconds']),
                                      **response_meta)
                if attempt==0:record['time_to_first_response_seconds']=time.monotonic()-began
                source_hash=file_hash(source)
                with self.lock:
                    duplicate=self.source_index.get(source_hash)
                    if duplicate is None:self.source_index[source_hash]=(m,c,i)
                record['source_sha256']=source_hash
                if duplicate is not None and duplicate!=(m,c,i):record['duplicate_of']=list(duplicate)
                if paired and attempt==0 and ('no-repair',c,i) not in self.tasks:
                    # Shared response artifact, no second first-generation call.
                    paired_start=time.monotonic()
                    self.admit('no-repair',c,i,source,plan)
                    record['paired_no_repair_validation_seconds']=time.monotonic()-paired_start
                validation_start=time.monotonic()
                if self.admission_profile:begin_evidence(d)
                pending_validation=(attempt,source)
                ok,review=self.normalize(source,d,plan,False)
                diagnostics=json.dumps(review,ensure_ascii=False)
                if ok:
                    comp=self.compile(d/'normalized.rs',d/'candidate','coverage')
                    attempt_record['normalized_coverage_compile_success']=comp['returncode']==0
                    diagnostics=(d/'candidate.compile.log').read_text(errors='replace')
                    if comp['returncode']==0:
                        if self.admit(m,c,i,source,plan):
                            attempt_record.update(accepted=True,compile_validation_seconds=time.monotonic()-validation_start)
                            attempt_record['compiler_runs']=getattr(self.worker_local,'compile_runs',[])[compile_before:]
                            record['accepted']=True;record['status']='accepted'
                            if self.admission_profile:
                                attempt_record['validation_evidence']=capture_attempt(d,attempt,source,
                                    True,{'method':m,'crate':c,'slot':i})
                                pending_validation=None
                            return
                        diagnostics=json.dumps(load(d/'outcome.json'),ensure_ascii=False)
                        for logname in ['original-wrapper.compile.log','original-wrapper-probe.log','probe.log']:
                            log=d/logname
                            if log.exists():diagnostics+='\n'+log.read_text(errors='replace')[-10000:]
                attempt_record['compile_validation_seconds']=time.monotonic()-validation_start
                attempt_record['compiler_runs']=getattr(self.worker_local,'compile_runs',[])[compile_before:]
                if self.admission_profile:
                    evidence=capture_attempt(d,attempt,source,False,
                                             {'method':m,'crate':c,'slot':i})
                    attempt_record['validation_evidence']=evidence
                    if repair_mode and self.admission_profile.get('repair_tasks'):
                        instruction=repair_task(load(plan)['plan'],source.read_text(),evidence['diagnosis'])
                        save(d/f'attempt-{attempt}.repair-task.json',instruction)
                        attempt_record['same_failure_fingerprint']=any(
                            a.get('validation_evidence',{}).get('diagnosis',{}).get('code')==evidence['diagnosis']['code']
                            for a in record['attempts'][:-1])
                    pending_validation=None
                    diagnostics=repair_feedback(evidence)
                with self.lock:self.generation[(m,c,i)]=copy.deepcopy(record);self.update()
                if attempt<max_calls-1:
                    if repair_mode:
                        selected=None
                        if isinstance(self.admission_profile.get('replanning'),dict) and evidence['diagnosis']['code'] in (
                            'core_compile_error','dependency_identity_mismatch','target_not_reached','input_influence_unobserved'):
                            selected=self.replan(initial_plan,d,m,c)
                            record['plan_lineage']=artifact(d/'replanning/plan-lineage.json')
                        if selected:
                            plan=selected;record['active_plan']=artifact(plan)
                            base_prompt=self.parameter_prompt(plan,prompt)
                            seed=d/f'alternate-seed-{attempt+1}.rs'
                            emitted=command_run([str(self.planner_bin),'repair-seed',str(plan),str(seed)],d,self.env,d/f'alternate-seed-{attempt+1}.log',15)
                            if emitted['returncode']!=0:raise ModelBackendError('alternate repair seed emission failed')
                            original=parameter_repair.prompt(base_prompt,seed.read_text(),evidence['diagnosis'],plan)
                            save(d/'generation.json',record)
                        else:original=parameter_repair.prompt(base_prompt,source.read_text(),evidence['diagnosis'],plan)
                    else:
                        prefix=base_prompt if self.cost_policy else original
                        original=prefix+'\nCURRENT SOURCE:\n'+source.read_text()+'\nDIAGNOSTICS:\n'+diagnostics[-20000:]+'\nRemaining repairs: '+str(max_calls-attempt-1)+'. Keep the immutable planned core if supplied.'
            record['status']='generation_failed'
            self.failure(m,c,i,'generation_failed',f'no accepted source within {max_calls} model calls',str(last))
        except BudgetRejected as error:
            record.update(status='infrastructure_failed',budget_rejection=str(error))
            self.failure(m,c,i,'infrastructure_failed',str(error),{'policy_revision':self.cost_policy['revision']})
            if paired and ('no-repair',c,i) not in self.tasks:self.failure('no-repair',c,i,'infrastructure_failed','shared first response blocked by cost policy')
        except EmptyModelResponse as error:
            record['backend_error']='EmptyModelResponse';record['status']='generation_failed'
            record['attempts'][-1].update(empty_response=True)
            try:
                meta=json.loads(str(error));record['attempts'][-1].update(meta)
            except json.JSONDecodeError:
                record['attempts'][-1]['empty_response_detail']=str(error)
            self.failure(m,c,i,'generation_failed','empty model response; target not retried automatically',str(error))
            if paired and ('no-repair',c,i) not in self.tasks:
                self.failure('no-repair',c,i,'generation_failed','shared first response was empty')
        except ModelTimeout as error:
            record['backend_error']='ModelTimeout';record['status']='generation_timeout'
            self.failure(m,c,i,'generation_timeout',str(error))
            if paired and ('no-repair',c,i) not in self.tasks:
                self.failure('no-repair',c,i,'generation_timeout','shared first response timed out')
        except ModelBackendError as error:
            record['backend_error']='ModelBackendError';record['status']='infrastructure_failed'
            self.failure(m,c,i,'infrastructure_failed',str(error))
            if paired and ('no-repair',c,i) not in self.tasks:
                self.failure('no-repair',c,i,'infrastructure_failed','shared first response backend failed')
        except Exception as error:
            record['backend_error']=type(error).__name__;record['status']='infrastructure_failed'
            if record['attempts'] and not record['attempts'][-1]['response_received']:
                record['attempts'][-1]['model_seconds']=time.monotonic()-model_start
            self.failure(m,c,i,'infrastructure_failed','model/backend error: '+type(error).__name__)
            if paired and ('no-repair',c,i) not in self.tasks:self.failure('no-repair',c,i,'infrastructure_failed','shared first response unavailable')
        finally:
            if self.admission_profile and pending_validation:
                index,source=pending_validation
                record['attempts'][-1]['validation_evidence']=capture_attempt(d,index,source,
                    False,{'method':m,'crate':c,'slot':i})
            record.update(finished=True,elapsed_seconds=time.monotonic()-began,compile_validation_seconds=sum(a.get('compile_validation_seconds',0) for a in record['attempts']))
            save(d/'generation.json',record)
            with self.lock:self.generation[(m,c,i)]=copy.deepcopy(record);self.update()
    def submit_model(self,pool,m,c,i,prompt,plan=None,paired=False):
        key=(m,c,i)
        if key in self.recovery_skip:return None
        if key in self.tasks:return None
        if key in self.generation and self.generation[key].get('finished'):return None
        with self.lock:
            if key not in self.generation:
                self.generation[key]={'round':self.number,'method':m,'crate':c,'slot':i,
                    'queued_unix_seconds':time.time(),'attempts':[],'finished':False,'accepted':False}
        return pool.submit(self.model,m,c,i,prompt,plan,paired)
    def plans(self,c):
        v=self.protocol['versions'][c];directory=self.root/'plans'/self.round_name/c;directory.mkdir(parents=True,exist_ok=True)
        raw=self.runtime/'raw-rustdoc'/(c+'-'+v+'.json');catalog=directory/'catalog.json'
        subprocess.run([str(self.planner_bin),'import',str(raw),str(catalog),'--package',c],check=True,capture_output=True)
        cat=load(catalog);allowed={a['rustdoc_id'] for a in self.universes[c]['apis'] if a.get('public_name_body_definition')}
        # Dependencies must also be publicly named; private rustdoc items are not
        # imported as hidden constructors in the controlled generation catalog.
        cat['apis']=[a for a in cat['apis'] if a['id'] in allowed];save(catalog,cat)
        plans=[]
        for api in cat['apis']:
            path=directory/(api['id']+'.json')
            infer=['--infer-call-results'] if self.admission_profile and self.admission_profile.get('infer_call_results') else []
            r=subprocess.run([str(self.planner_bin),'plan',str(catalog),api['id'],str(path)]+infer,capture_output=True,timeout=30)
            if path.exists() and load(path)['status']=='planned':plans.append((api,path))
        save(directory/'support.json',{'public_catalog_apis':len(cat['apis']),'complete_candidates':len(plans),'public_definition_denominator':sum(a.get('public_name_body_definition',False) for a in self.universes[c]['apis'])})
        def select(random_order=False):
            pool=list(plans);random.Random(20261109+self.number*100003+sum(map(ord,c))).shuffle(pool)
            if not random_order:pool.sort(key=lambda item:sum(s['kind']=='call' for s in load(item[1])['plan']['steps']),reverse=True)
            usage={};chosen=[]
            while pool and len(chosen)<20:
                best=min(range(len(pool)),key=lambda n:usage.get(pool[n][0]['id'],0))
                item=pool.pop(best);chosen.append(item);usage[item[0]['id']]=usage.get(item[0]['id'],0)+1
            return chosen
        docs=[];index=load(raw)['index']
        for api in cat['apis']:
            docs.append({'id':api['id'],'path':api['path'],'owner':api['owner'],'inputs':api['inputs'],'output':api['output'],'generics':api['generics'],'unsupported':api['unsupported'],'documentation':index.get(api['id'],{}).get('docs','')})
        context=json.dumps(docs,ensure_ascii=False)
        if not self.cost_policy:context=context[:32000]
        save(directory/'selection.json',{'Full':[a['id'] for a,p in select()],'random-target':[a['id'] for a,p in select(True)],'policy':'public, complete bounded candidates; depth priority vs seeded random order; no coverage/compile outcomes used in selection','documentation_utf8_character_cap':None if self.cost_policy else 32000,'request_documentation_utf8_bytes':self.cost_policy['documentation_utf8_bytes'] if self.cost_policy else None})
        return catalog,cat,select(),select(True),context
    def source_from_plan(self,plan,d):
        out=d/'emitted';out.mkdir(exist_ok=True)
        subprocess.run([str(self.planner_bin),'emit',str(plan),str(out)],check=True,capture_output=True)
        return out/'src/main.rs'
    def crate(self,c,genpool):
        try:catalog,cat,full,rand,context=self.plans(c)
        except Exception as e:
            for m in self.protocol['methods']:
                if m not in ['RPG','RULF','deepSURF']:
                    for i in range(20):self.failure(m,c,i,'infrastructure_failed','catalog preparation: '+str(e))
            return
        prefix='Use only the pinned '+c+' '+self.protocol['versions'][c]+' default-feature library. Public documentation pool:\n'+context+'\n'
        free_prompt=prefix+'Return Rust source only. Define pub fn fuzz_target(data: &[u8]) with safe API calls driven by those bytes. No main, AFL dependency, filesystem/environment/network/process access or unsafe code. Fully qualify library calls. Avoid unbounded allocation and documented precondition violations. Output values must be kept observable with std::hint::black_box. Do not merely consume data and then call constant targets.'
        for i in range(20):
            self.submit_model(genpool,'LLM-only',c,i,free_prompt)
            rng=random.Random(20261109+self.number*100003+i+sum(map(ord,c)))
            length=sum(s['kind']=='call' for s in load(full[i][1])['plan']['steps']) if i<len(full) else 4
            apis=rng.sample(cat['apis'],min(max(1,length),len(cat['apis']))) if cat['apis'] else []
            save(self.directory('random-API+LLM',c,i)/'required-names.json',[a['path'].split('::')[-1] for a in apis])
            self.submit_model(genpool,'random-API+LLM',c,i,free_prompt+'\nAttempt these randomly sampled API names in this order, constructing valid arguments; do not omit impossible calls to obtain compilation:\n'+json.dumps([a['path'] for a in apis]))
            for m,selected in [('GRAFT',full),('deterministic-emitter',full),('no-repair',full),('syntactic+LLM',full),('no-cross-argument-unification',full),('random-target',rand)]:
                if (m,c,i) in self.recovery_skip:continue
                if (m,c,i) in self.tasks:continue
                if i>=len(selected):self.failure(m,c,i,'planning_unknown','fewer than 20 complete public target candidates in finite planner; denominator retained');continue
                if m=='no-repair':continue
                api,plan=selected[i];d=self.directory(m,c,i)
                if m in ['syntactic+LLM','no-cross-argument-unification']:
                    altered=d/'plan.json';flag='--syntactic' if m=='syntactic+LLM' else '--independent-arguments'
                    infer=['--infer-call-results'] if self.admission_profile and self.admission_profile.get('infer_call_results') else []
                    r=subprocess.run([str(self.planner_bin),'plan',str(catalog),api['id'],str(altered),flag]+infer,capture_output=True,timeout=30)
                    if r.returncode!=0:self.failure(m,c,i,'planning_unknown','counterfactual planner could not construct target',load(altered));continue
                    plan=altered
                try:
                    source=self.source_from_plan(plan,d)
                    if m=='deterministic-emitter':
                        if parameter_repair.enabled(self.admission_profile):
                            source=d/'deterministic-seed.rs'
                            subprocess.run([str(self.planner_bin),'repair-seed',str(plan),str(source)],check=True,capture_output=True)
                        self.admit(m,c,i,source,plan);continue
                    promptfile=d/'core-prompt.txt';subprocess.run([str(self.planner_bin),'prompt',str(plan),str(promptfile)],check=True)
                    self.submit_model(genpool,m,c,i,prefix+promptfile.read_text(),plan,m=='GRAFT')
                except Exception as e:
                    self.failure(m,c,i,'infrastructure_failed',str(e))
                    if m=='GRAFT':self.failure('no-repair',c,i,'infrastructure_failed','Full preparation unavailable')
    def baseline(self,m):
        tool=m.lower();summary=PORT/(f'generator-preflight-formal-round{self.number}/'+tool+'/summary.json')
        if not summary.exists():raise RuntimeError('native generator summary missing '+m)
        reports={r['crate']:r for r in load(summary)}
        if self.fixed_generation:
            reports={c:dict(r,generation_measurement='reused_prepared_inventory',original_report=artifact(summary)) for c,r in reports.items()}
        with self.lock:self.generator_runs[m]=reports
        for c in self.protocol['versions']:
            directory=PORT/(f'generator-preflight-formal-round{self.number}/rpg/'+c) if m=='RPG' else PORT/(f'rulf-work-formal-round{self.number}/'+c.replace('-','_')+'-work')
            sources=sorted(directory.rglob('test*.rs')) if directory.exists() else []
            row=reports.get(c,{})
            for i in range(20):
                if (m,c,i) in self.recovery_skip:continue
                if (m,c,i) in self.tasks:continue
                if i<len(sources):self.admit(m,c,i,sources[i],baseline=True)
                else:
                    g=row.get('generation',{})
                    status='generation_timeout' if g.get('timed_out') else ('no_target' if g.get('returncode')==0 else 'infrastructure_failed')
                    self.failure(m,c,i,status,'native generator produced fewer target slots',row)
    def deepsurf(self):
        directory=self.root/'deepsurf-native'/self.round_name
        pending=set(self.protocol['versions'])
        while pending:
            for c in sorted(pending):
                report=directory/c/'outcomes.json'
                if not report.exists():continue
                data=load(report)
                with self.lock:self.generator_runs.setdefault('deepSURF',{})[c]=data['generation']
                for row in data['targets']:
                    if ('deepSURF',c,row['slot']) in self.recovery_skip:continue
                    if ('deepSURF',c,row['slot']) in self.tasks:continue
                    if row.get('source'):self.admit('deepSURF',c,row['slot'],Path(row['source']),baseline=True)
                    else:self.failure('deepSURF',c,row['slot'],row['status'],row.get('reason',''),row)
                pending.remove(c)
            if not pending:break
            finished=directory/'finished.json'
            if finished.exists():
                for c in pending:
                    for i in range(20):self.failure('deepSURF',c,i,'infrastructure_failed','native deepSURF preparation ended without this crate result',load(finished))
                break
            if self.deep_process is not None and self.deep_process.poll() is not None:
                exit_record={'pid':self.deep_process.pid,'returncode':self.deep_process.returncode,
                             'finished_marker':str(finished)}
                for c in pending:
                    for i in range(20):
                        if ('deepSURF',c,i) not in self.tasks:
                            self.failure('deepSURF',c,i,'infrastructure_failed',
                                         'native deepSURF preparation process exited before this crate result',
                                         exit_record)
                break
            # This wait yields the CPU; the live progress file remains readable.
            time.sleep(5)
    def finish(self):
        for future in self.fuzz_futures:future.result()
        self.fuzz_pool.shutdown()
        tasks=[self.tasks.get(k,v) for k,v in self.identities.items()]
        save(self.root/self.round_name/'tasks.json',{'round':self.number,'protocol_sha256':self.protocol['_file_sha256'],'tasks':tasks})
        results=list(self.results.values());summary=summarize(self.protocol,results,self.universes,self.number);save(self.root/self.round_name/'summary.json',summary)
        self.last_metrics_write=0;self.update()
        if self.fixed_generation and summary['status']=='complete':
            from run_fixed_generation import seal_first
            seal_first(self.root,self.protocol)
        if not self.fixed_generation:
            publish(self.protocol,self.root,ROOT.parent/'GRAFTPaper',self.number,summary,
                    generation=load(self.root/self.round_name/'all-method-generation.json'))
        progress=load(self.root/'progress.json');progress.update(status='round_attempt_finished',round_status=summary['status'],formal_round_complete=summary['status']=='complete');save(self.root/'progress.json',progress);save(self.root/self.round_name/'progress.json',progress)
        return summary

def run_one(engine):
    with cf.ThreadPoolExecutor(max_workers=16) as genpool:
        with cf.ThreadPoolExecutor(max_workers=4) as prep:
            futures=[prep.submit(engine.baseline,m) for m in ['RPG','RULF']]
            futures += [prep.submit(engine.crate,c,genpool) for c in engine.protocol['versions']]
            # A separate native deepSURF process supplies each crate result as
            # it finishes; it never borrows another generator's targets.
            futures.append(prep.submit(engine.deepsurf))
            for future in futures:
                try:future.result()
                except Exception as error:print('preparation exception',type(error).__name__,str(error),flush=True)
    return engine.finish()

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--auto-second',action='store_true');p.add_argument('--resume',action='store_true')
    p.add_argument('--cost-policy',type=Path,default=ROOT/'experiments/cost-control/policy.json')
    p.add_argument('--legacy-model-budget',action='store_true',help='Explicit historical reproduction only; high-thinking costs apply')
    p.add_argument('--admission-profile',type=Path,help='Opt-in v2 profile; requires a new batch')
    p.add_argument('--planner-bin',type=Path,help='Isolated checked planner binary for the v2 profile')
    p.add_argument('--harness-bin',type=Path,help='Isolated normalization binary for the v2 profile')
    a=p.parse_args();a.root=a.root.resolve()
    cost_policy=None if a.legacy_model_budget else a.cost_policy.resolve()
    control=a.root/'control.json'
    if not control.exists():save(control,{'auto_second':a.auto_second,'stop_after_round':2,'reason':'explicit user instruction: automatically start round two after round one unless superseded'})
    for number in [1,2] if a.auto_second else [1]:
        round_name=f'round-{number:02d}';summary_path=a.root/round_name/'summary.json'
        publication_protocol=load(a.root/'protocol.json') if (a.root/'protocol.json').exists() else {}
        publication=publication_directory(publication_protocol,ROOT.parent/'GRAFTPaper',number)/'publication.json'
        if summary_path.exists() and load(summary_path).get('status')=='complete' and publication.exists():
            print(f'{round_name} already complete and published; not regenerated',flush=True)
            continue
        prep=a.root/'preparation'/round_name
        if prep.exists() and any(prep.rglob('*')) and not a.resume:
            raise RuntimeError(f'{round_name} has an interrupted preparation attempt; rerun with --resume; do not overwrite sealed artifacts')
        if number>1:
            policy=load(control)
            if not policy.get('auto_second') or policy.get('stop_after_round',1)<number:break
            command=[sys.executable,str(ROOT/'scripts/run_generator_preflight.py'),'--root',str(PORT),'--project',str(PORT/'joint-compatibility-v2'),'--suffix',f'formal-round{number}','--versions',str(ROOT/'experiments/week4/common-versions.json'),'--output',str(a.root/f'native-baselines-round-{number:02d}')]
            native=command_run(command,ROOT,clean_env(),a.root/f'native-baselines-round-{number:02d}.log',1200)
            if native['returncode']!=0:raise RuntimeError('fresh native baseline generation failed; next round not started')
        # DeepSURF preparation overlaps the other methods; its result is consumed
        # by Engine.deepsurf and all methods share one round completion barrier.
        deep_dir=a.root/'deepsurf-native'/f'round-{number:02d}';deep_dir.mkdir(parents=True,exist_ok=True)
        deep_command=[sys.executable,str(ROOT/'scripts/prepare_deepsurf_round.py'),'--root',str(a.root),'--round',str(number)]
        deep_command+=['--cost-policy',str(cost_policy)] if cost_policy else ['--legacy-model-budget']
        # Freeze/check the protocol before starting any paid native subprocess.
        engine=Engine(a.root,number,resume=a.resume,cost_policy=cost_policy,
                      admission_profile=a.admission_profile,planner_bin=a.planner_bin,harness_bin=a.harness_bin)
        process_path=deep_dir/'process.json'
        if a.resume and process_path.exists():
            deep=ExistingProcess(int(load(process_path)['pid']))
        else:
            with (deep_dir/'launcher.log').open('wb') as deep_log:
                deep=subprocess.Popen(deep_command,cwd=ROOT,env=clean_env(),stdout=deep_log,stderr=subprocess.STDOUT,start_new_session=True)
            save(process_path,{'pid':deep.pid,'command':deep_command})
        engine.deep_process=deep
        summary=run_one(engine)
        if summary['status']!='complete':
            print('Round incomplete: keep missing measurements unavailable; automatic next round is waiting for infrastructure repair.',flush=True)
            break
        # publish() above must succeed before a new independent generation batch.
if __name__=='__main__':main()
