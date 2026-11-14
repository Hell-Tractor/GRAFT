#!/usr/bin/env python3
"""Run a single supplemental DeepSURF track, preserving all original records."""
import argparse,concurrent.futures as cf,hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
from launch_controlled_round import Engine
from run_experiment_rounds import save,summarize
import run_round1_recovery as recovery

ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
 source=a.source.resolve();out=a.output.resolve()
 if not a.execute:print(json.dumps({'status':'check_only','source':str(source),'output':str(out),'native_memory_gib':12,'native_workers':1,'fuzz_workers':8,'seconds_per_target':300,'graft_modified':False}));return
 recovery.enforce_cgroup()
 out.mkdir(parents=True,exist_ok=True)
 if (out/'execution-started.json').exists():raise RuntimeError('one repair execution only; inspect existing results instead of retrying')
 save(out/'execution-started.json',{'pid':os.getpid(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'started':time.time(),'source':str(source)})
 (out/'runner.pid').write_text(str(os.getpid()))
 for name in ['runtime','deepsurf-port','tools']:
  if not (out/name).exists():(out/name).symlink_to(source/name,target_is_directory=True)
 protocol=json.loads((source/'protocol.json').read_text());protocol['methods']=['deepSURF'];protocol['rounds']=1;protocol['operator_start_policy']='one separately labelled baseline repair track; never regenerate GRAFT or start round two';protocol['publication_namespace']='review-baselines/deepsurf-repair-20261113'
 save(out/'protocol.json',protocol)
 engine=Engine(out,number=1,cost_policy=ROOT/'experiments/cost-control/policy.json',admission_profile=ROOT/'experiments/admission-v2/repair-v5.json',planner_bin=source/'tools/graft-plan',harness_bin=source/'tools/graft-harness',fixed_generation=True,defer_fuzz=True,compile_workers=2,model_workers=1,fuzz_workers=8,preserve_frozen_protocol=True)
 recovery.RECOVERY=out
 results={}
 # Recover the demonstrated inventory adapter error first; other crates follow once.
 crates=['bytes']+[c for c in protocol['versions'] if c!='bytes']
 for c in crates:
  recovery.health_gate()
  cmd=[sys.executable,str(ROOT/'scripts/repair_deepsurf_baseline.py'),'--root',str(out),'--reuse-from',str(source),'--round','1','--only',c,'--native-workers','1','--container-label','graft-review-deepsurf']
  with (out/(c+'-native.log')).open('w') as log:
   r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=2100)
  native=out/'deepsurf-native/round-01'/c/'outcomes.json'
  if r.returncode or not native.exists():raise RuntimeError('native repair did not save terminal outcome: '+c)
  inventory=json.loads(native.read_text())
  for item in inventory['targets']:
   i=item['slot']
   if 'source' in item:
    engine.admit('deepSURF',c,i,Path(item['source']),baseline=True)
   else:engine.failure('deepSURF',c,i,item['status'],item.get('reason','native outcome'),item)
  states={('deepSURF',c,i):('ready_not_started' if engine.tasks.get(('deepSURF',c,i),{}).get('status')=='ready' else engine.tasks.get(('deepSURF',c,i),{}).get('status','infrastructure_failed')) for i in range(20)}
  recovery.phase_b(out,protocol,states,8)
  results[c]={'states':{}}
  for i in range(20):
   result=out/'round-01/deepSURF'/c/str(i)/'result.json'
   if result.exists():engine.results[('deepSURF',c,i)]=json.loads(result.read_text())
  for i in range(20):
   st=engine.results.get(('deepSURF',c,i),{}).get('status',engine.tasks[('deepSURF',c,i)]['status']);results[c]['states'][st]=results[c]['states'].get(st,0)+1
  save(out/'repair-progress.json',{'status':'running','crates':results,'updated':time.time(),'original_batch_untouched':True})
 engine.fuzz_pool.shutdown(wait=True)
 final=summarize(protocol,list(engine.results.values()),engine.universes,1)
 final['repair_track']={'source':str(source),'original_results_replaced':False,'generation_retries':'only originally unfinished native generation, once; complete generation reused after inventory adapter correction','crates':results}
 save(out/'repair-summary.json',final)
 save(out/'repair-progress.json',{'status':'stopped','round_status':final['status'],'crates':results,'updated':time.time()})
 print(json.dumps({'status':final['status'],'output':str(out)}))
if __name__=='__main__':main()
