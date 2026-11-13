#!/usr/bin/env python3
"""One isolated deepSURF repair track; inherits generation and cost evidence, never edits GRAFT or original results."""
import argparse, concurrent.futures as cf, json, os, re, shlex, shutil, subprocess, sys, time, uuid
from pathlib import Path
from experiment_batch import read_key
from run_experiment_rounds import command_run, save
from model_cost_control import read_policy, read_ledger, spent
from deepsurf_repair_support import selected_sources

ROOT=Path(__file__).resolve().parents[1]
PORT=ROOT.parent.parent/'graft-baseline-port-20261109'
DEEPSURF_IMAGE='deepsurf:graft-ready'

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--round',type=int,required=True);p.add_argument('--only');p.add_argument('--only-list',help='comma-separated repair subset; does not seal the round finished marker');p.add_argument('--analysis-only',action='store_true')
    p.add_argument('--cost-policy',type=Path,default=ROOT/'experiments/cost-control/policy.json');p.add_argument('--legacy-model-budget',action='store_true')
    p.add_argument('--reuse-from',type=Path,required=True);p.add_argument('--native-workers',type=int,default=1);p.add_argument('--container-memory-gib',type=int,default=12);p.add_argument('--container-pids',type=int,default=256);p.add_argument('--container-cpus',type=int,default=4);p.add_argument('--analysis-timeout',type=int,default=900);p.add_argument('--generation-timeout',type=int,default=900);p.add_argument('--container-label',default='graft-recovery');p.add_argument('--skip-existing',action='store_true');a=p.parse_args()
    if a.native_workers<1 or a.container_memory_gib<1 or a.container_cpus<1:raise ValueError('invalid native recovery limits')
    cost_policy=None if a.legacy_model_budget else read_policy(a.cost_policy)
    root=a.root.resolve();source_batch=a.reuse_from.resolve();base=root/'deepsurf-native'/f'round-{a.round:02d}';base.mkdir(parents=True,exist_ok=True)
    versions=json.loads((ROOT/'experiments/week4/common-versions.json').read_text())
    if 'versions' in versions:versions=versions['versions']
    build=base/'analysis-build';build.mkdir(exist_ok=True);build.chmod(0o777)
    scripts=ROOT/'scripts'
    env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ['API_KEY','TOKEN','SECRET','AUTHORIZATION'])}
    def docker(network,work,kind):
        name=f'graft-r{a.round}-{kind}-{uuid.uuid4().hex[:12]}'
        command=['docker','run','--rm','--name',name,'--label',f'{a.container_label}=true','--memory',f'{a.container_memory_gib}g','--memory-swap',f'{a.container_memory_gib}g','--pids-limit',str(a.container_pids),'--cpus',str(a.container_cpus),'--init','--network',network,'--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--cpuset-cpus','48-55','--tmpfs','/tmp:rw,exec,size=512m','--mount',f'type=bind,src={root},dst={root}','--mount',f'type=bind,src={PORT},dst={PORT},readonly','--mount',f'type=bind,src={source_batch},dst={source_batch},readonly','--mount',f'type=bind,src={scripts},dst=/opt/graft-scripts,readonly','-e','LD_LIBRARY_PATH=/root/deepSURF/code/rust/build/host/stage2/lib:/root/deepSURF/code/rust/build/host/stage2/lib/rustlib/x86_64-unknown-linux-gnu/lib','--workdir',str(work)]
        return command,name
    def cleanup_container(name):
        subprocess.run(['docker','stop','-t','10',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        remaining=subprocess.run(['docker','container','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode
        return remaining!=0
    def run_container(command,log,timeout,generation,run_env=None):
        name=command[command.index('--name')+1]
        started_container=time.time()
        record={'name':name,'memory_gib':a.container_memory_gib,'pids':a.container_pids,'cpus':a.container_cpus,'timeout_seconds':timeout}
        generation.setdefault('containers',[]).append(record)
        try:
            result=command_run(command,ROOT,run_env if run_env is not None else env,log,timeout)
            return result
        finally:
            try:
                events=subprocess.run(['docker','events','--since',str(int(started_container)-1),'--until',str(int(time.time())+1),'--filter','container='+name,'--filter','event=oom','--format','{{json .}}'],capture_output=True,text=True,timeout=5)
                record['oom_events']=[json.loads(line) for line in events.stdout.splitlines() if line.strip()] if events.returncode==0 else None
            except (subprocess.TimeoutExpired,json.JSONDecodeError):record['oom_events']=None
            record['cleaned']=cleanup_container(name)
            if not record['cleaned']:raise RuntimeError('experiment container cleanup failed: '+name)
    def run(c,v):
        work=base/c;work.mkdir(exist_ok=True);work.chmod(0o777);lib=work/'library'
        if (work/'outcomes.json').exists():return
        old=source_batch/'deepsurf-native'/f'round-{a.round:02d}'/c
        previous=json.loads((old/'outcomes.json').read_text()) if (old/'outcomes.json').exists() else {}
        prior_gen=previous.get('generation',{})
        if prior_gen.get('generation',{}).get('returncode')==0 and (old/'library/deepSURF/deepsurf_fuzzing_targets.txt').exists():
            candidates=selected_sources(old/'library')
            save(work/'outcomes.json',{'generation':{'crate':c,'version':v,'round':a.round,'status':'reused_completed_generation_fixed_inventory_adapter','inherited_outcome':str(old/'outcomes.json'),'native_selected_count':len(candidates),'new_model_calls':0},'targets':[{'crate':c,'slot':i,'source':str(candidates[i])} if i<len(candidates) else {'crate':c,'slot':i,'status':'no_target','reason':'completed native selected inventory has fewer than 20 targets'} for i in range(20)]})
            return
        if not lib.exists():shutil.copytree(PORT/'vendor-v2'/(c+'-'+v),lib)
        lib.chmod(0o777)
        report_dir=lib/'deepSURF/report'
        # cap-drop ALL root cannot write host-owned 0755 output directories.
        # Only fresh experiment output directories are made writable.
        (lib/'deepSURF').mkdir(exist_ok=True);(lib/'deepSURF').chmod(0o777)
        generation={'crate':c,'version':v,'round':a.round,'native_generator':'deepSURF','port':'original generation and called-URAPI feedback; compile-only AFL shim uses exact pinned rlibs; common real AFL execution follows','started_unix_seconds':time.time()}
        suffixes=('urapi','constrs','constrs-llm','urapi-llm','enums','traits','deps','trait_fns')
        local_reports=list(report_dir.glob(c.replace('-','_')+'_*.urapi.json')) if report_dir.exists() else []
        local_complete=False
        if len(local_reports)==1 and (work/'check-config.json').exists():
            prefix=local_reports[0].name[:-len('.urapi.json')]
            local_complete={prefix+'.'+suffix+'.json' for suffix in suffixes}<={f.name for f in report_dir.iterdir()}
        if local_complete:
            generation['analysis']={'status':'reused_complete_local_analysis','source':str(work/'check-config.json'),'returncode':0}
        elif report_dir.exists():
            cleanup,name=docker('none',work,'cleanup')
            script='chmod 777 '+shlex.quote(str(report_dir))+' && find '+shlex.quote(str(report_dir))+' -maxdepth 1 -type f -name '+shlex.quote(c.replace('-','_')+'_*.urapi.json')+' -delete'
            cleanup+=['--entrypoint','/bin/sh',DEEPSURF_IMAGE,'-c',script]
            cleaned=run_container(cleanup,work/'cleanup.log',60,generation)
            if cleaned['returncode']!=0:raise RuntimeError('native report cleanup failed; see cleanup.log')
        try:
            prior_reports=list((old/'library/deepSURF/report').glob(c.replace('-','_')+'_*.urapi.json'))
            if prior_gen.get('analysis',{}).get('returncode')==0 and len(prior_reports)==1 and (old/'check-config.json').exists():
                report_dir.mkdir(parents=True,exist_ok=True);report_dir.chmod(0o777)
                prefix=prior_reports[0].name[:-len('.urapi.json')]
                companions=list(prior_reports[0].parent.glob(prefix+'.*.json'))
                required={prefix+'.'+suffix+'.json' for suffix in suffixes}
                if not required<={f.name for f in companions}:raise RuntimeError('original analysis bundle is incomplete')
                for companion in companions:shutil.copy2(companion,report_dir/companion.name)
                generation['inherited_analysis_files']=[str(f) for f in sorted(companions)]
                checks=json.loads((old/'check-config.json').read_text())
                checks['output']=str(work/'native-checks');(work/'native-checks').mkdir(exist_ok=True);(work/'native-checks').chmod(0o777)
                save(work/'check-config.json',checks)
                generation['analysis']={'status':'reused_verified_original_analysis','source':str(old/'check-config.json'),'returncode':0}
            elif not local_complete:
                analysis,name=docker('none',work,'analysis')
                analysis += ['--entrypoint','python3',DEEPSURF_IMAGE,'/opt/graft-scripts/deep_analysis_container.py',c,v,str(work),str(PORT),str(build)]
                generated=run_container(analysis,work/'analysis.log',a.analysis_timeout,generation)
                generation['analysis']=generated
                if generated['returncode']!=0:raise RuntimeError('native analysis failed; see analysis.log')
            if a.analysis_only:return
            if cost_policy:
                save(work/'model-cost-policy.json',cost_policy)
                generation['model_cost_policy_revision']=cost_policy['revision']
            reports=list((lib/'deepSURF/report').glob(c.replace('-','_')+'_*.urapi.json'))
            if len(reports)!=1:raise RuntimeError('native analysis report missing or ambiguous')
            # The same frozen public documentation pool used by controlled LLM
            # arms, transported without invoking a second rustdoc implementation.
            runtime_root=root/'runtime'
            if not runtime_root.exists():runtime_root=source_batch/'runtime'
            raw=json.loads((runtime_root/'raw-rustdoc'/(c+'-'+v+'.json')).read_text())
            universe=json.loads((runtime_root/'universes/api_universes'/(c+'.json')).read_text())
            docs=[{'path':x['path'] if 'path' in x else x.get('qualified_name',x.get('name')),'documentation':raw['index'].get(x['rustdoc_id'],{}).get('docs','')} for x in universe['apis'] if x.get('public_name_body_definition')]
            docs_path=work/'frozen-public-docs.json';docs_path.write_text(json.dumps(docs,ensure_ascii=False) if cost_policy else json.dumps(docs,ensure_ascii=False)[:32000])
            wrapper=work/'bin';wrapper.mkdir(exist_ok=True)
            cargo=wrapper/'cargo';cargo.write_text('#!/bin/sh\nexec python3 /opt/graft-scripts/deep_native_cargo.py "$@"\n');cargo.chmod(0o755)
            metrics=work/'llm-calls';metrics.mkdir(exist_ok=True);metrics.chmod(0o777)
            prior_ledger=old/'llm-calls/crate-model-cost.json'
            if prior_ledger.exists() and not (metrics/'crate-model-cost.json').exists():
                shutil.copy2(prior_ledger,metrics/'crate-model-cost.json')
                generation['inherited_cost_ledger']=str(prior_ledger)
            generation['cost_policy']='prior calls and reservations retained; no target-budget reset'
            callenv=dict(env,LLM_API_KEY=read_key(Path.home()/'.config/go-exit/deepseek.env'))
            command,name=docker('bridge',work,'generate')
            variables={'PATH':str(wrapper)+':/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                'SURF_WORKING_PATH':str(lib),'GLOBAL_DATA_PATH':'/root/deepSURF/code/global_data',
                'SURF_CHECK_CONFIG':str(work/'check-config.json'),'SURF_DISABLE_TARGET_BUILD':'1',
                'SURF_DISABLE_REMOVE_LAST_TARGET':'1','SURF_ENABLE_LLMS':'1','SURF_SKIP_OPTION':'condskip',
                'SURF_FROZEN_DOCS':str(docs_path),'SURF_METRICS_DIR':str(metrics),
                'CARGO_NET_OFFLINE':'true','LLM_BACKEND':'deepseek-v4-pro'}
            if cost_policy:variables['GRAFT_MODEL_COST_POLICY']=str(work/'model-cost-policy.json')
            for k,vv in variables.items():command+=['-e',k+'='+vv]
            # Docker inherits this variable from the launcher environment. The
            # command and saved witnesses never contain the credential value.
            command+=['-e','LLM_API_KEY','--entrypoint',str(root/'deepsurf-port/build/release/harness_generator'),DEEPSURF_IMAGE,str(reports[0])[:-len('.urapi.json')]]
            generation['generation']=run_container(command,work/'generation.log',a.generation_timeout,generation,callenv)
            if cost_policy:
                ledger=read_ledger(metrics/'crate-model-cost.json')
                generation['model_cost']={'peak_estimate_or_reserved_cny':str(spent(ledger)),
                    'paid_or_reserved_calls':len(ledger['entries']),
                    'usage_unknown_calls':sum(e['status']!='usage_reported' for e in ledger['entries']),
                    'ledger':str(metrics/'crate-model-cost.json')}
            selected=lib/'deepSURF/deepsurf_fuzzing_targets.txt'
            native_complete=selected.exists() and generation['generation']['returncode']==0
            if not native_complete and not generation['generation']['timed_out']:
                raise RuntimeError('native generation or LLM integration did not finish; see generation.log')
            names=set(re.findall(r'(?m)^\s+\* (\S+)\s*$',selected.read_text())) if selected.exists() else set()
            candidates=selected_sources(lib)
            generation.update(native_selected_count=len(candidates),native_complete=native_complete,
                llm_call_count=len(list(metrics.glob('*-timing.json'))),
                llm_call_timing=[json.loads(p.read_text()) for p in sorted(metrics.glob('*-timing.json'))])
            targets=[]
            for i in range(20):
                if i<len(candidates):targets.append({'crate':c,'slot':i,'source':str(candidates[i])})
                else:targets.append({'crate':c,'slot':i,'status':'no_target' if native_complete else 'generation_timeout','reason':'native selected target inventory contains fewer than 20 sources'})
        except Exception as e:
            generation['infrastructure_error']=str(e)
            targets=[{'crate':c,'slot':i,'status':'infrastructure_failed','reason':str(e)} for i in range(20)]
        generation['finished_unix_seconds']=time.time();generation['elapsed_seconds']=time.time()-generation['started_unix_seconds']
        save(work/'outcomes.json',{'generation':generation,'targets':targets})
    try:
        with cf.ThreadPoolExecutor(max_workers=a.native_workers) as pool:
            selected=set(a.only_list.split(',')) if a.only_list else ({a.only} if a.only else set(versions))
            unknown=selected-set(versions)
            if unknown:raise ValueError('unknown deepSURF crates: '+','.join(sorted(unknown)))
            futures=[pool.submit(run,c,v) for c,v in versions.items() if c in selected]
            for f in futures:f.result()
    finally:
        if not a.only and not a.only_list and not a.analysis_only:
            save(base/'finished.json',{'finished_unix_seconds':time.time()})
if __name__=='__main__':main()
