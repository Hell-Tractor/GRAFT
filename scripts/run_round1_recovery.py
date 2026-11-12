#!/usr/bin/env python3
"""Safety-gated recovery of the interrupted admission-v5 first round."""
import argparse
import concurrent.futures as cf
import fcntl
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

from run_experiment_rounds import (checked_file, execute, file_hash, load, publish,
                                   save, summarize, UPSTREAM_FAILURES)
from run_fixed_generation import validate_protocol

ROOT=Path(__file__).resolve().parents[1]
BATCH=Path('/home/cym/workspace/graft-admission-full-fixedgen-20261110')
RECOVERY=Path('/home/cym/workspace/graft-round1-recovery-20261112')
HANDOFF=Path('/home/cym/workspace/graft-incident-20261112/round1-recovery-handoff-20261112.json')
LIMITS={'memory_high_bytes':64*1024**3,'memory_max_bytes':80*1024**3,
        'memory_swap_max':0,'tasks_max':1024,'compile_workers':4,'model_workers':4,
        'max_fuzz_workers':24,'replay_workers':4,'task_address_space_bytes':2*1024**3,
        'native_memory_gib':12,'native_pids':256,'native_cpus':4,'native_timeout_seconds':900}
UNRESOLVED={('LLM-only','idna',0),('random-API+LLM','idna',0),
            ('syntactic+LLM','fixedbitset',17),('no-cross-argument-unification','fixedbitset',17)}
ALLOWED_SCHEDULER_CHANGES={str(ROOT/'scripts'/name) for name in
    ('launch_controlled_round.py','prepare_deepsurf_round.py','run_experiment_rounds.py')}


def verify_records_allow_scheduler(value):
    if isinstance(value,dict):
        if 'path' in value and 'sha256' in value:
            if str(Path(value['path']).resolve()) in ALLOWED_SCHEDULER_CHANGES:return
            checked_file(value)
        else:
            for item in value.values():verify_records_allow_scheduler(item)
    elif isinstance(value,list):
        for item in value:verify_records_allow_scheduler(item)


def verify_recovery_inputs(batch):
    frozen=load(batch/'fixed-generation-inputs.json')
    if Path(frozen['protocol']['path'])!=(batch/'protocol.json').resolve():
        raise ValueError('batch identity moved or cloned')
    verify_records_allow_scheduler(frozen)
    protocol=load(batch/'protocol.json');protocol['_file_sha256']=file_hash(batch/'protocol.json')
    validate_protocol(protocol)
    if frozen['protocol']['sha256']!=protocol['_file_sha256']:
        raise ValueError('protocol changed')
    changes={}
    for path in sorted(ALLOWED_SCHEDULER_CHANGES):
        relative=str(Path(path).relative_to(ROOT))
        old=frozen.get('implementation',{}).get(relative)
        changes[relative]={'old':old,'current':{'path':path,'sha256':file_hash(path)},
                           'scope':'scheduler/resource safety only; admission and scientific inputs unchanged'}
    return protocol,changes


def process_alive(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().split()[2]!='Z' and os.kill(pid,0) is None
    except (OSError,IndexError,FileNotFoundError):return False


def experiment_processes():
    text=subprocess.run(['pgrep','-af','run_fixed_generation|launch_controlled_round|prepare_deepsurf_round|afl-fuzz'],
                        capture_output=True,text=True).stdout
    return [line for line in text.splitlines() if line and 'run_round1_recovery' not in line
            and 'pgrep -af' not in line]


def owned_containers(label):
    text=subprocess.run(['docker','ps','-q','--filter','label='+label+'=true'],
                        capture_output=True,text=True).stdout
    return [x for x in text.splitlines() if x]


def cgroup_limits():
    try:
        relative=Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
        base=Path('/sys/fs/cgroup')/relative.lstrip('/')
        def read(name):
            p=base/name;return p.read_text().strip() if p.exists() else None
        return {'path':str(base),'memory.high':read('memory.high'),'memory.max':read('memory.max'),
                'memory.swap.max':read('memory.swap.max'),'pids.max':read('pids.max')}
    except (OSError,IndexError):return {}


def enforce_cgroup():
    value=cgroup_limits()
    expected={'memory.high':str(LIMITS['memory_high_bytes']),'memory.max':str(LIMITS['memory_max_bytes']),
              'memory.swap.max':str(LIMITS['memory_swap_max']),'pids.max':str(LIMITS['tasks_max'])}
    if any(value.get(k)!=v for k,v in expected.items()):
        raise RuntimeError('execute must run inside the frozen recovery cgroup: '+json.dumps(value))
    return value


def expected_keys(protocol):
    slots=protocol.get('target_slots',20)
    return {(m,c,i) for m in protocol['methods'] for c in protocol['versions'] for i in range(slots)}


def state_of(batch,protocol,key):
    m,c,i=key;base=batch/'preparation/round-01'/m/c/str(i)
    result=batch/'round-01'/m/c/str(i)/'result.json'
    if result.exists():return load(result).get('status','unknown')
    outcome=base/'outcome.json'
    if outcome.exists():return load(outcome).get('status','unknown')
    task=base/'task.json'
    if task.exists():
        t=load(task);return 'interrupted' if (batch/'round-01'/m/c/str(i)/'started.json').exists() else 'ready_not_started'
    if key in UNRESOLVED:return 'unsettled_request'
    generation=base/'generation.json'
    if generation.exists() and load(generation).get('finished'):return load(generation).get('status','generation_terminal')
    return 'not_prepared'


def reconcile(batch,protocol):
    states={k:state_of(batch,protocol,k) for k in expected_keys(protocol)}
    counts={}
    for value in states.values():counts[value]=counts.get(value,0)+1
    required=len(protocol['methods'])*len(protocol['versions'])*protocol.get('target_slots',20)
    if protocol.get('fixed_target_slots') not in (None,required):
        raise ValueError('fixed denominator changed')
    if len(states)!=required:raise ValueError('fixed denominator changed')
    return states,counts


def verify_inherited_results(batch,states):
    checked=0
    for (m,c,i),status in states.items():
        if status!='measured':continue
        result=load(batch/'round-01'/m/c/str(i)/'result.json')
        export=Path(result['coverage_export']) if 'coverage_export' in result else batch/'round-01'/m/c/str(i)/'coverage-export.json'
        if file_hash(export)!=result['coverage_sha256']:raise ValueError('inherited coverage changed '+str(export))
        checked+=1
    return checked


def protected_inventory(batch):
    generation={}
    for path in (batch/'preparation/round-01').glob('*/*/*/generation.json'):
        generation[str(path.relative_to(batch))]=file_hash(path)
    completed={}
    for path in (batch/'round-01').glob('*/*/*/result.json'):
        result=load(path)
        if result.get('status') in ('measured','replay_failed','fuzz_failed'):
            completed[str(path.relative_to(batch))]=file_hash(path)
    return {'generation_records':generation,'completed_fuzz_results':completed}


def verify_protected_inventory(batch,before):
    after=protected_inventory(batch)
    for group in before:
        for path,digest in before[group].items():
            if after[group].get(path)!=digest:
                raise RuntimeError('protected prior result was modified or removed: '+path)
    return {'protected_generation_records':len(before['generation_records']),
            'protected_completed_fuzz_results':len(before['completed_fuzz_results'])}


def health_gate():
    mem={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,value=line.split(':',1);mem[key]=int(value.split()[0])*1024
    if mem['MemAvailable']<32*1024**3:raise RuntimeError('host MemAvailable below recovery growth threshold')
    if mem['SwapFree']<mem['SwapTotal']-64*1024**2:raise RuntimeError('host swap is in use; recovery growth blocked')
    if owned_containers('graft-recovery'):
        raise RuntimeError('owned recovery containers already exist')


def save_progress(phase,value):
    RECOVERY.mkdir(parents=True,exist_ok=True)
    save(RECOVERY/'progress.json',{'status':'running','phase':phase,'updated_unix_seconds':time.time(),**value})


def phase_b(batch,protocol,states,workers):
    universes={c:load(checked_file(r)) for c,r in protocol['universes'].items()}
    keys=sorted(k for k,v in states.items() if v=='ready_not_started')
    priorities=keys[:16];rest=keys[16:]
    waves=[('validation-1',priorities[:8]),('validation-2',priorities[8:16]),
           ('scale-'+str(workers),rest)]
    reports=[]
    for name,wave in waves:
        if not wave:continue
        health_gate();save_progress('ready-fuzz',{'wave':name,'remaining':len(wave),'workers':workers if name.startswith('scale') else 8})
        width=8 if name.startswith('validation') else workers
        replay_slots=threading.Semaphore(LIMITS['replay_workers'])
        cpus=sorted(os.sched_getaffinity(0))[:width]
        if len(cpus)<width:raise RuntimeError('insufficient CPU affinity for recovery wave')
        free=list(cpus);pending={};queue=iter(wave);results=[]
        with cf.ThreadPoolExecutor(max_workers=width) as pool:
            while True:
                while free:
                    key=next(queue,None)
                    if key is None:break
                    m,c,i=key;task=load(batch/'preparation/round-01'/m/c/str(i)/'task.json')
                    cpu=free.pop();dest=batch/'round-01'/m/c/str(i)
                    future=pool.submit(execute,task,protocol,1,dest,[cpu],universes[c],
                                       replay_slots=replay_slots,recovery_limits=True)
                    pending[future]=cpu
                if not pending:break
                done,_=cf.wait(pending,return_when=cf.FIRST_COMPLETED)
                for future in done:
                    free.append(pending.pop(future));results.append(future.result())
        reports.append({'wave':name,'tasks':len(wave),'status_counts':{s:sum(r['status']==s for r in results)
            for s in sorted({r['status'] for r in results})}})
        save_progress('ready-fuzz-wave-complete',{'wave':name,'report':reports[-1]})
    save(RECOVERY/'phase-b.json',{'waves':reports})
    return reports


def run_native_missing(batch,destination,missing):
    reports=[]
    policy=load(batch/'fixed-generation-inputs.json')['cost_policy']['path']
    for crate in sorted(missing):
        health_gate()
        save_progress('native-recovery',{'crate':crate,'remaining':sorted(missing)})
        command=[sys.executable,str(ROOT/'scripts/prepare_deepsurf_round.py'),'--root',str(batch),'--round','1',
                 '--cost-policy',policy,
                 '--only',crate,'--native-workers','1','--container-memory-gib',str(LIMITS['native_memory_gib']),
                 '--container-pids',str(LIMITS['native_pids']),'--container-cpus',str(LIMITS['native_cpus']),
                 '--analysis-timeout',str(LIMITS['native_timeout_seconds']),'--generation-timeout',str(LIMITS['native_timeout_seconds']),
                 '--container-label','graft-recovery','--skip-existing']
        result=subprocess.run(command,capture_output=True,text=True,timeout=LIMITS['native_timeout_seconds']+120)
        reports.append({'crate':crate,'returncode':result.returncode,'stdout':result.stdout[-4000:],'stderr':result.stderr[-4000:]})
        if result.returncode:raise RuntimeError('safe native recovery failed for '+crate)
    save(destination/'phase-c-native.json',{'runs':reports})


def prepare_missing(batch,destination):
    from launch_controlled_round import Engine
    inputs=load(batch/'fixed-generation-inputs.json')
    existing={p.parent.name for p in (batch/'deepsurf-native/round-01').glob('*/outcomes.json')}
    protocol=load(batch/'protocol.json');missing=set(protocol['versions'])-existing
    run_native_missing(batch,destination,missing)
    engine=Engine(batch,resume=True,cost_policy=inputs['cost_policy']['path'],
        admission_profile=inputs['profile']['path'],planner_bin=batch/'tools/graft-plan',harness_bin=batch/'tools/graft-harness',
        fixed_generation=True,defer_fuzz=True,recovery_skip=UNRESOLVED,compile_workers=LIMITS['compile_workers'],
        model_workers=LIMITS['model_workers'],fuzz_workers=LIMITS['max_fuzz_workers'])
    with cf.ThreadPoolExecutor(max_workers=LIMITS['model_workers']) as genpool:
        with cf.ThreadPoolExecutor(max_workers=LIMITS['compile_workers']) as prep:
            futures=[prep.submit(engine.baseline,m) for m in ('RPG','RULF')]
            futures += [prep.submit(engine.crate,c,genpool) for c in engine.protocol['versions']]
            futures.append(prep.submit(engine.deepsurf))
            errors=[]
            for future in futures:
                try:future.result()
                except Exception as error:errors.append(type(error).__name__+': '+str(error))
    engine.fuzz_pool.shutdown(wait=True)
    save(destination/'phase-c-preparation.json',{'errors':errors,'new_tasks':sum((batch/'preparation/round-01').glob('*/*/*/task.json')),
        'unresolved_isolated':[list(x) for x in sorted(UNRESOLVED)]})
    if errors:raise RuntimeError('preparation completed with stage errors: '+json.dumps(errors))


def recovery_summary(batch,protocol):
    universes={c:load(checked_file(r)) for c,r in protocol['universes'].items()}
    results=[]
    for m in protocol['methods']:
        for c in protocol['versions']:
            for i in range(20):
                result=batch/'round-01'/m/c/str(i)/'result.json'
                if result.exists():results.append(load(result));continue
                task=batch/'preparation/round-01'/m/c/str(i)/'task.json'
                if task.exists():
                    t=load(task)
                    if t['status'] in UPSTREAM_FAILURES:
                        results.append({'method':m,'crate':c,'slot':i,'status':t['status'],'evidence':t.get('evidence')})
                    else:results.append({'method':m,'crate':c,'slot':i,'status':'interrupted' if (batch/'round-01'/m/c/str(i)/'started.json').exists() else 'ready_not_started'})
                elif (m,c,i) in UNRESOLVED or not (batch/'preparation/round-01'/m/c/str(i)/'outcome.json').exists():
                    results.append({'method':m,'crate':c,'slot':i,'status':'infrastructure_failed','reason':'unresolved recovery slot'})
                else:
                    o=load(batch/'preparation/round-01'/m/c/str(i)/'outcome.json')
                    results.append({'method':m,'crate':c,'slot':i,'status':o.get('status','infrastructure_failed')})
    summary=summarize(protocol,results,universes,1)
    summary['recovery']={'status':'partial' if summary['status']!='complete' else 'complete',
        'unresolved_requests':[list(x) for x in sorted(UNRESOLVED)],'generation_seal_created':False}
    save(RECOVERY/'round1-recovery-summary.json',summary)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,default=BATCH);p.add_argument('--recovery',type=Path,default=RECOVERY)
    p.add_argument('--execute',action='store_true');p.add_argument('--check-only',action='store_true')
    p.add_argument('--through-ready',action='store_true',help='stop after currently ready targets; do not generate new work')
    p.add_argument('--paper',type=Path,default=ROOT.parent/'GRAFTPaper')
    a=p.parse_args()
    if a.execute==a.check_only:p.error('choose exactly one of --check-only and --execute')
    batch=a.batch.resolve();protocol,changes=verify_recovery_inputs(batch)
    a.recovery.mkdir(parents=True,exist_ok=True)
    amendment=a.recovery/'recovery-amendment.json'
    amendment_value={'schema':'graft-round1-recovery-amendment-v1','batch':str(batch),
        'protocol_sha256':protocol['_file_sha256'],'allowed_scheduler_changes':changes,
        'admission_algorithm_changed':False,'scientific_inputs_changed':False}
    if amendment.exists() and load(amendment)!=amendment_value:
        raise RuntimeError('recovery amendment changed after freeze')
    if not amendment.exists():save(amendment,amendment_value)
    states,counts=reconcile(batch,protocol)
    protected=protected_inventory(batch)
    if a.check_only:
        checked=verify_inherited_results(batch,states);processes=experiment_processes();containers=owned_containers('graft-recovery')
        if processes or containers:raise RuntimeError('experiment processes/containers exist before recovery')
        result={'status':'checked_live_recovery_state','new_model_calls':0,'fuzz_jobs':0,
            'fixed_slots':len(states),'state_counts':counts,'inherited_measurements_hashed':checked,
            'unsettled_requests':[list(x) for x in sorted(UNRESOLVED)],'required_limits':LIMITS,
            'current_cgroup':cgroup_limits(),'queued_tasks':0,
            'protected_generation_records':len(protected['generation_records']),
            'protected_completed_fuzz_results':len(protected['completed_fuzz_results'])}
        save(a.recovery/'check-only.json',result);print(json.dumps(result,indent=2));return
    a.recovery.mkdir(parents=True,exist_ok=True)
    with (a.recovery/'recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (a.recovery/'execution-started.json').exists() and not (a.recovery/'progress.json').exists():
            raise RuntimeError('execution start marker exists without progress')
        save(a.recovery/'execution-started.json',{'batch':str(batch),'protocol_sha256':protocol['_file_sha256'],
            'limits':LIMITS,'started_unix_seconds':time.time()})
        (a.recovery/'runner.pid').write_text(str(os.getpid()))
        cgroup=enforce_cgroup()
        save_progress('reconciled',{'state_counts':counts,'cgroup':cgroup,
            'protected':verify_protected_inventory(batch,protected)})
        phase_b(batch,protocol,states,LIMITS['max_fuzz_workers'])
        verify_protected_inventory(batch,protected)
        if not a.through_ready:
            prepare_missing(batch,a.recovery)
            verify_protected_inventory(batch,protected)
            states,counts=reconcile(batch,protocol)
            phase_b(batch,protocol,states,LIMITS['max_fuzz_workers'])
            verify_protected_inventory(batch,protected)
        states,counts=reconcile(batch,protocol)
        summary=recovery_summary(batch,protocol)
        generation=batch/'round-01/all-method-generation.json'
        if generation.exists():publish(protocol,batch,a.paper.resolve(),1,summary,generation=load(generation))
        save_progress('stopped',{'state_counts':counts,'round_status':summary['status'],'second_round_started':False})
        print(json.dumps({'status':summary['status'],'state_counts':counts,'published_partial':generation.exists(),
            'next_round_started':False},indent=2))


if __name__=='__main__':main()
