#!/usr/bin/env python3
"""Generate once, seal every slot, then replay six paired AFL repetitions.

Preparation/checks never call a model. Generation is imported only for round one;
repetitions use sealed tasks and the common executor, with publication barriers.
"""
import argparse,concurrent.futures as cf,fcntl,hashlib,json,os,shutil,subprocess,sys,time
from decimal import Decimal
from pathlib import Path
from run_experiment_rounds import (save,load,file_hash,checked_file,check_tasks,execute,
    summarize,publish,publication_directory,UPSTREAM_FAILURES)

ROOT=Path(__file__).resolve().parents[1]
MODE='fixed-generation-six-fuzz-v1'

def artifact(path):
    path=Path(path).resolve();return {'path':str(path),'sha256':file_hash(path)}

def verify_records(value):
    if isinstance(value,dict):
        if 'path' in value and 'sha256' in value:checked_file(value)
        else:
            for v in value.values():verify_records(v)
    elif isinstance(value,list):
        for v in value:verify_records(v)

def cost_files(batch):
    paths=set()
    for name in ['preparation','deepsurf-native']:
        paths.update((batch/name).rglob('*model-cost.json'))
    paths.update((batch/'model-cost-ledgers').rglob('*.json'))
    return sorted(paths)

def cost_summary(paths):
    entries={};methods={}
    for p in paths:
        parts=p.parts
        method='deepSURF' if 'deepsurf-native' in parts else parts[parts.index('round-01')+1]
        for e in load(p).get('entries',[]):
            if e['id'] in entries and entries[e['id']]!=e:raise ValueError('Conflicting duplicate model charge')
            if e['id'] in methods and methods[e['id']]!=method:raise ValueError('Charge method binding differs')
            entries[e['id']]=e;methods[e['id']]=method
    per_method={m:{'paid_or_reserved_calls':sum(methods[k]==m for k in entries),
        'spent_or_reserved_cny':str(sum((Decimal(v['charged_cny'])for k,v in entries.items()if methods[k]==m),Decimal(0)))}for m in sorted(set(methods.values()))}
    return {'accounting':'once; deduplicate mirrored target/crate ledgers by request id',
        'by_method':per_method,'paid_or_reserved_calls':len(entries),'spent_or_reserved_cny':str(sum((Decimal(e['charged_cny']) for e in entries.values()),Decimal(0))),
        'unknown_or_reserved_calls':sum(e['status']!='usage_reported' for e in entries.values())}

def admitted_costs(paths,metrics_data):
    billing=cost_summary(paths)
    for method,row in billing['by_method'].items():
        admitted=sum(r['eventually_admitted']for r in metrics_data['rows']if r['method']==method)
        charge=Decimal(row['spent_or_reserved_cny']);ratio=charge/admitted if admitted else None
        row.update(admitted_targets=admitted,cost_per_admitted_cny=str(ratio)if ratio is not None else None,
                   cost_gate_passed=(ratio<Decimal('.25')if ratio is not None else charge==0))
    billing['cost_gate_passed']=all(r['cost_gate_passed']for r in billing['by_method'].values())
    return billing

def validate_protocol(p):
    if (p.get('generation_mode')!=MODE or p.get('generation_repetitions')!=1
        or p.get('fuzz_repetitions')!=6 or p.get('afl_seeds_per_generation')!=6
        or p['rounds']!=6 or p['workers_per_target']!=1):raise ValueError('Not a one-generation/six-seed protocol')
    if p['seconds_per_target']!=300 and p.get('execution_scope')!='development_smoke':
        raise ValueError('Formal experiments require 300 seconds per target')

def verify_tasks(p,manifest,number):
    tasks=check_tasks(p,manifest,number)
    for t in tasks:
        if t['version']!=p['versions'][t['crate']]:raise ValueError('Task version changed')
        if t['status']!='ready':continue
        for name in ['replay_binary','replay_adapter']:checked_file(t[name])
        corpus=load(t['initial_corpus_manifest']['path']);expected={str(checked_file(r).resolve())for r in corpus['files']}
        def argument(command,flag):
            if command.count(flag)!=1:raise ValueError('Ambiguous execution flag '+flag)
            return command[command.index(flag)+1]
        initial=Path(argument(t['fuzz_command'],'-i')).resolve()
        if expected!={str(x.resolve())for x in initial.rglob('*')if x.is_file()}:raise ValueError('Initial corpus inventory changed')
        if Path(argument(t['replay_command'],'--initial')).resolve()!=initial:raise ValueError('Replay initial corpus changed')
        if (argument(t['fuzz_command'],'-o')!='{run_dir}/afl'
            or argument(t['replay_command'],'--afl-output')!='{run_dir}/afl'
            or argument(t['replay_command'],'--work')!='{run_dir}/replayed'):
            raise ValueError('Cross-round corpus/output reuse forbidden')
        if (argument(t['replay_command'],'--binary')!=t['replay_binary']['path']
            or t['fuzz_command'][-1]!=t['binary']['path']):raise ValueError('Command/binary binding mismatch')
    return tasks

def seal_first(batch,p):
    batch=Path(batch);validate_protocol(p);path=batch/'generation-seal.json'
    if path.exists():return verify_seal(batch,p)
    tasks=load(batch/'round-01/tasks.json');verify_tasks(p,tasks,1)
    if load(batch/'round-01/summary.json')['status']!='complete':raise ValueError('Incomplete round cannot seed repetitions')
    metrics=batch/'round-01/all-method-generation.json'
    if len(load(metrics)['rows'])!=len(p['methods'])*len(p['versions']):raise ValueError('Missing generation statistics')
    costs=cost_files(batch);billing=cost_summary(costs)
    if billing['unknown_or_reserved_calls']:raise ValueError('Unresolved usage prevents repetitions')
    billing=admitted_costs(costs,load(metrics))
    evidence=[]
    for prefix in ['preparation/round-01','deepsurf-native/round-01']:
        for f in sorted((batch/prefix).rglob('*')):
            if f.is_file() and (f.name in ['generation.json','plan.json','task.json','outcomes.json'] or f.name.startswith(('request-','raw-response-'))):evidence.append(artifact(f))
    seal={'schema':'graft-fixed-generation-seal-v1','protocol':artifact(batch/'protocol.json'),
        'tasks':artifact(batch/'round-01/tasks.json'),'first_summary':artifact(batch/'round-01/summary.json'),
        'generation_metrics':artifact(metrics),'generation_evidence':evidence,
        'cost_ledgers':[artifact(f)for f in costs],'cost_summary':billing,
        'generation_repetitions':1,'fuzz_repetitions':6}
    save(path,seal);return verify_seal(batch,p)

def verify_seal(batch,p):
    seal=load(batch/'generation-seal.json');verify_records(seal)
    if seal['protocol']['sha256']!=p['_file_sha256']:raise ValueError('Sealed protocol mismatch')
    if cost_files(batch)!=[Path(r['path'])for r in seal['cost_ledgers']]:raise ValueError('New/deleted cost ledger after generation')
    verify_tasks(p,load(seal['tasks']['path']),1)
    if admitted_costs(cost_files(batch),load(seal['generation_metrics']['path']))!=seal['cost_summary']:raise ValueError('Sealed cost accounting differs')
    return seal

def repetition_manifest(batch,p,number):
    seal=verify_seal(batch,p)
    return {'round':number,'protocol_sha256':p['_file_sha256'],
        'generation_source':artifact(batch/'generation-seal.json'),
        'tasks':load(seal['tasks']['path'])['tasks']}

def barrier(batch,p,paper,number):
    rd=batch/f'round-{number:02d}';path=rd/'publication-barrier.json'
    if path.exists():
        value=load(path);verify_records(value)
        if value['protocol_sha256']!=p['_file_sha256']:raise ValueError('Publication barrier protocol changed')
        return
    summary=load(rd/'summary.json')
    if summary['status']!='complete':raise ValueError('Incomplete measurements block next round')
    destination=publication_directory(p,paper,number);publication=load(destination/'publication.json')
    if publication['protocol_sha256']!=p['_file_sha256'] or publication['round']!=number:raise ValueError('Publication identity mismatch')
    if file_hash(publication['pdf'])!=publication['pdf_sha256']:raise ValueError('Published PDF changed')
    if load(destination/'summary.json')!=summary:raise ValueError('Published summary differs')
    stats=load(destination/'statistics.json')
    if stats['independent_generation_rounds']!=1 or stats['independent_afl_seeds_per_generation']!=number:raise ValueError('Publication generation/seed accounting differs')
    save(path,{'protocol_sha256':p['_file_sha256'],'round':number,'summary':artifact(rd/'summary.json'),
        'publication':artifact(destination/'publication.json'),'statistics':artifact(destination/'statistics.json'),
        'pdf':artifact(publication['pdf'])})

def controlled_stop(batch,number):
    c=load(batch/'control.json')
    if c.get('auto_second') is not False:raise ValueError('Legacy auto_second must remain disabled')
    if type(c.get('pause')) is not bool or type(c.get('stop_after_round')) is not int or not 1<=c['stop_after_round']<=6:
        raise ValueError('Invalid fixed-generation pause/stop control')
    return c['pause'] or number>c['stop_after_round']

def verify_inputs(batch):
    frozen=load(batch/'fixed-generation-inputs.json')
    if Path(frozen['protocol']['path'])!=(batch/'protocol.json').resolve():raise ValueError('Batch identity moved or cloned')
    verify_records(frozen)
    p=load(batch/'protocol.json');p['_file_sha256']=file_hash(batch/'protocol.json');validate_protocol(p)
    if frozen['protocol']['sha256']!=p['_file_sha256']:raise ValueError('Protocol changed')
    return p

def execute_repetition(batch,p,universes,number):
    rd=batch/f'round-{number:02d}';manifest=repetition_manifest(batch,p,number)
    path=rd/'tasks.json'
    if path.exists():
        if load(path)!=manifest:raise ValueError('Repetition manifest changed')
    else:save(path,manifest)
    tasks=verify_tasks(p,manifest,number)
    cpus=sorted(os.sched_getaffinity(0))[:p['fuzz_workers']]
    if len(cpus)<p['fuzz_workers']:raise ValueError('Insufficient CPU affinity')
    tasks=sorted(tasks,key=lambda t:hashlib.sha256(f"{p['root_seed']}:schedule:{number}:{t['method']}:{t['crate']}:{t['slot']}".encode()).digest())
    save(rd/'execution-order.json',{'cpus':cpus,'tasks':[{k:t[k]for k in ['method','crate','slot']}for t in tasks]})
    results=[]
    with cf.ThreadPoolExecutor(max_workers=len(cpus)) as pool:
        queue=iter(tasks);free=list(cpus);pending={}
        while True:
            while free:
                t=next(queue,None)
                if t is None:break
                cpu=free.pop();dest=rd/t['method']/t['crate']/str(t['slot'])
                pending[pool.submit(execute,t,p,number,dest,[cpu],universes[t['crate']])]=cpu
            if not pending:break
            done,_=cf.wait(pending,return_when=cf.FIRST_COMPLETED)
            for f in done:free.append(pending.pop(f));results.append(f.result())
    verify_seal(batch,p)
    summary=summarize(p,results,universes,number);save(rd/'summary.json',summary);return summary

def first_generation(batch,resume):
    # This is the only branch that imports model/native-generation orchestration.
    from launch_controlled_round import Engine,run_one,clean_env,ExistingProcess
    inputs=load(batch/'fixed-generation-inputs.json');deepdir=batch/'deepsurf-native/round-01';deepdir.mkdir(parents=True,exist_ok=True)
    process_file=deepdir/'process.json'
    if (batch/'first-generation-started.json').exists() and not resume:raise ValueError('Use --resume for interrupted generation; never reset budgets')
    if process_file.exists() and 'pid' not in load(process_file):raise ValueError('Native launch interrupted before PID was sealed; reconcile without resending')
    expected_image=inputs.get('native_container_image')
    if expected_image:
        image=subprocess.run(['docker','image','inspect','--format','{{.Id}}',expected_image['tag']],capture_output=True,text=True,check=True).stdout.strip()
        if image!=expected_image['id']:raise ValueError('Native container image changed')
    engine=Engine(batch,resume=resume,cost_policy=inputs['cost_policy']['path'],admission_profile=inputs['profile']['path'],
        planner_bin=batch/'tools/graft-plan',harness_bin=batch/'tools/graft-harness',fixed_generation=True)
    command=[sys.executable,str(ROOT/'scripts/prepare_deepsurf_round.py'),'--root',str(batch),'--round','1','--cost-policy',inputs['cost_policy']['path']]
    save(batch/'first-generation-started.json',{'command':command,'started_unix_seconds':time.time()})
    if process_file.exists():engine.deep_process=ExistingProcess(load(process_file)['pid'])
    else:
        save(process_file,{'status':'starting','command':command})
        with (deepdir/'launcher.log').open('wb')as log:
            process=subprocess.Popen(command,cwd=ROOT,env=clean_env(),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        save(process_file,{'pid':process.pid,'command':command});engine.deep_process=process
    return run_one(engine)

def run(batch,paper,through=6,resume=False,check_only=False,generate=first_generation):
    batch,paper=Path(batch).resolve(),Path(paper).resolve()
    if not 1<=through<=6:raise ValueError('through-round must be 1..6')
    p=verify_inputs(batch)
    if check_only:
        if (batch/'generation-seal.json').exists():verify_seal(batch,p)
        return {'status':'checked_sealed_generation' if (batch/'generation-seal.json').exists() else 'checked_preparation_only',
            'new_model_calls':0,'fuzz_jobs':0,'through_round':through}
    with (batch/'fixed-generation.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for number in range(1,through+1):
            verify_inputs(batch)
            if controlled_stop(batch,number):return {'status':'paused_at_round_boundary','next_round':number}
            if number>1:barrier(batch,p,paper,number-1)
            if number==1 and not (batch/'generation-seal.json').exists():
                result=generate(batch,resume)
                if result['status']!='complete':return {'status':'round_incomplete','round':1}
            seal=verify_seal(batch,p)
            rd=batch/f'round-{number:02d}'
            if not (rd/'publication-barrier.json').exists():
                if number>1:
                    if rd.exists() and any(rd.iterdir()) and not resume:raise ValueError('Use --resume for an existing repetition')
                    universe={c:load(checked_file(r))for c,r in p['universes'].items()}
                    result=execute_repetition(batch,p,universe,number)
                    if result['status']!='complete':
                        publish(p,batch,paper,number,result,generation=load(seal['generation_metrics']['path']))
                        return {'status':'round_incomplete','round':number}
                publish(p,batch,paper,number,load(rd/'summary.json'),generation=load(seal['generation_metrics']['path']))
                barrier(batch,p,paper,number)
            else:barrier(batch,p,paper,number)
            if not seal['cost_summary'].get('cost_gate_passed',False):
                save(batch/'fixed-generation-progress.json',{'status':'cost_gate_failed','published_round':number,'cost_counted_once':seal['cost_summary']})
                return {'status':'cost_gate_failed','published_round':number}
            save(batch/'fixed-generation-progress.json',{'status':'complete' if number==6 else 'round_complete',
                'published_round':number,'generation_repetitions':1,'completed_afl_seeds':number,'cost_counted_once':seal['cost_summary']})
        return {'status':'complete' if through==6 else 'stopped_after_requested_round','published_round':through}

def prepare(source,batch,planner,harness,calibration):
    from launch_controlled_round import Engine,PORT,AFL,LLVM
    source,batch,calibration=Path(source).resolve(),Path(batch).resolve(),Path(calibration).resolve()
    profile=ROOT/'experiments/admission-v2/repair-v5.json';policy=ROOT/'experiments/cost-control/policy.json'
    old=load(calibration/'calibration-manifest.json');gate=load(calibration/'calibration-gate-review.json')
    if not gate['overall_pass'] or old['admission_profile']!=artifact(profile):raise ValueError('Final calibration/profile gate not satisfied')
    # Only scheduling/statistics may differ from the admission calibration.
    for name,record in old['implementation'].items():
        if name not in ['scripts/launch_controlled_round.py','scripts/run_experiment_rounds.py']:checked_file(record)
    for name,path in [('planner',planner),('harness',harness)]:
        if file_hash(path)!=old['binaries'][name]['sha256']:raise ValueError('Calibration binary differs')
    if batch.is_relative_to(source):raise ValueError('Cannot prepare inside the source batch')
    if batch.exists():raise ValueError('New batch directory required; no paid variant reset')
    cpus=set(os.sched_getaffinity(0))
    if len(cpus)<56 or not set(range(48,56))<=cpus:raise ValueError('Current native adapter requires CPUs 48..55 and 48 fuzz slots')
    inventory=[]
    for method in ['RPG','RULF']:
        report=PORT/'generator-preflight-formal-round1'/method.lower()/'summary.json'
        inventory.append(artifact(report))
        for c in load(ROOT/'experiments/week5/protocol.json')['versions']:
            directory=PORT/('generator-preflight-formal-round1/rpg/'+c if method=='RPG' else 'rulf-work-formal-round1/'+c.replace('-','_')+'-work')
            inventory.extend(artifact(f)for f in sorted(directory.rglob('test*.rs')))
    image_tag='deepsurf:graft-ready'
    image_id=subprocess.run(['docker','image','inspect','--format','{{.Id}}',image_tag],capture_output=True,text=True,check=True).stdout.strip()
    execution_tools=[artifact(f)for f in [AFL,LLVM/'llvm-profdata',LLVM/'llvm-cov',Path(sys.executable)]]
    subprocess.run([sys.executable,str(ROOT/'scripts/prepare_cost_control_batch.py'),'--source',str(source),'--destination',str(batch)],check=True,capture_output=True)
    tools=batch/'tools';tools.mkdir()
    shutil.copy2(planner,tools/'graft-plan');shutil.copy2(harness,tools/'graft-harness')
    save(batch/'control.json',{'pause':False,'stop_after_round':6,'auto_second':False})
    engine=Engine(batch,cost_policy=policy,admission_profile=profile,planner_bin=tools/'graft-plan',harness_bin=tools/'graft-harness',fixed_generation=True)
    engine.fuzz_pool.shutdown(wait=True)
    protocol=load(batch/'protocol.json')
    files=list((ROOT/'scripts').glob('*.py'))+[ROOT/'experiments/week5/protocol.json',profile,policy,
        ROOT/'experiments/admission-v2/parameter-hints-v3.json']
    refs={str(p.relative_to(ROOT)):artifact(p)for p in sorted(files)}
    # Retain executable source/config snapshots as evidence; live drift is rejected.
    for relative in refs:
        dest=batch/'frozen-implementation'/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/relative,dest)
    save(batch/'fixed-generation-inputs.json',{'schema':'graft-fixed-generation-inputs-v1','protocol':artifact(batch/'protocol.json'),
        'profile':artifact(profile),'cost_policy':artifact(policy),'binaries':{'planner':artifact(tools/'graft-plan'),'harness':artifact(tools/'graft-harness')},
        'native_container_image':{'tag':image_tag,'id':image_id},'native_inventories_reused':inventory,'execution_tools':execution_tools,
        'native_generator':artifact(batch/'deepsurf-port/build/release/harness_generator'),
        'implementation':refs,'calibration_gate':artifact(calibration/'calibration-gate-review.json'),
        'shared_runtime':protocol['runtime_evidence'],'common_versions':protocol['common_versions'],
        'shared_lock':protocol['shared_lock'],'shared_manifest':protocol['shared_manifest'],'universes':protocol['universes']})
    return {'status':'prepared_not_executed','batch':str(batch),'model_calls':0,'fuzz_jobs':0,'fixed_target_slots':len(protocol['methods'])*len(protocol['versions'])*protocol['target_slots']}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True)
    mode=p.add_mutually_exclusive_group(required=True)
    for flag in ['prepare','execute','check-only']:mode.add_argument('--'+flag,action='store_true')
    p.add_argument('--source-batch',type=Path);p.add_argument('--planner-bin',type=Path);p.add_argument('--harness-bin',type=Path)
    p.add_argument('--calibration',type=Path,default=ROOT.parent.parent/'graft-admission-v5-calibration-20261110')
    p.add_argument('--paper',type=Path,default=ROOT.parent/'GRAFTPaper');p.add_argument('--through-round',type=int,default=6);p.add_argument('--resume',action='store_true')
    a=p.parse_args()
    if a.prepare:
        if not all([a.source_batch,a.planner_bin,a.harness_bin]):p.error('prepare needs source-batch, planner-bin, harness-bin')
        result=prepare(a.source_batch,a.batch,a.planner_bin,a.harness_bin,a.calibration)
    else:result=run(a.batch,a.paper,a.through_round,a.resume,a.check_only)
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
