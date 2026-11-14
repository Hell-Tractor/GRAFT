#!/usr/bin/env python3
"""Consume a separately generated native inventory under the frozen common admission/execution track."""
import argparse,json,os,time
from pathlib import Path
from launch_controlled_round import Engine
from run_experiment_rounds import save,summarize
import run_round1_recovery as recovery
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--method',choices=['RuMono','FRIES','SyRust','deepSURF'],required=True);p.add_argument('--crates',help='explicit comma-separated subset; report remains a separate subset track');p.add_argument('--source',type=Path,required=True);p.add_argument('--inventory',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true');p.add_argument('--resume',action='store_true',help='resume sealed tasks without regenerating or repeating completed fuzzing');a=p.parse_args()
 if not a.execute:print(json.dumps({'status':'check_only','method':a.method,'seconds':300,'workers':8,'output':str(a.output)}));return
 limits=recovery.cgroup_limits()
 if limits.get('memory.max')!='17179869184' or limits.get('memory.swap.max')!='0':raise RuntimeError('requires 16 GiB memory and no swap cgroup')
 out=a.output.resolve();source=a.source.resolve();out.mkdir(parents=True,exist_ok=True)
 existing=(out/'execution-started.json').exists()
 if existing and not a.resume:raise RuntimeError('one execution only; use explicit --resume after inspecting prior evidence')
 if a.resume and not existing:raise RuntimeError('--resume requires an existing execution')
 if not existing:save(out/'execution-started.json',{'pid':os.getpid(),'method':a.method,'limits':limits,'started':time.time()})
 else:save(out/('resume-'+str(time.time_ns())+'.json'),{'pid':os.getpid(),'limits':limits,'started':time.time(),'inventory':str(a.inventory)})
 (out/'runner.pid').write_text(str(os.getpid()))
 if not (out/'runtime').exists():(out/'runtime').symlink_to(source/'runtime',target_is_directory=True)
 protocol=json.loads((source/'protocol.json').read_text());protocol.update(methods=[a.method],rounds=1,publication_namespace='review-baselines/'+a.method.lower()+'-20261113',operator_start_policy='one separate first round; no GRAFT generation or automatic repetitions')
 if a.crates:
  subset=set(a.crates.split(','))
  if not subset<=set(protocol['versions']):raise ValueError('unknown crate subset')
  protocol['versions']={c:v for c,v in protocol['versions'].items() if c in subset}
  protocol['universes']={c:v for c,v in protocol['universes'].items() if c in subset}
  protocol['definition_count']=sum(sum(x.get('public_name_body_definition',False) for x in json.loads(Path(v['path']).read_text())['apis']) for v in protocol['universes'].values())
  protocol['subset_track']=sorted(subset)
 if existing:
  frozen=json.loads((out/'protocol.json').read_text())
  for field in ('methods','versions','universes','seconds_per_target'):
   if frozen.get(field)!=protocol.get(field):raise ValueError('resume protocol mismatch: '+field)
 else:save(out/'protocol.json',protocol)
 engine=Engine(out,cost_policy=ROOT/'experiments/cost-control/policy.json',admission_profile=ROOT/'experiments/admission-v2/repair-v5.json',planner_bin=source/'tools/graft-plan',harness_bin=source/'tools/graft-harness',fixed_generation=True,defer_fuzz=True,compile_workers=2,model_workers=1,fuzz_workers=8,preserve_frozen_protocol=True,resume=a.resume)
 # Engine restores executable tasks/results; terminal admission/generation
 # failures are sealed separately and must also survive a baseline resume.
 if a.resume:
  for outcome in (out/'preparation/round-01'/a.method).glob('*/*/outcome.json'):
   record=json.loads(outcome.read_text());key=(record['method'],record['crate'],record['slot'])
   if key not in engine.tasks:
    engine.results[key]=record;engine.tasks[key]=dict(engine.identities[key],status=record['status']);engine.identities[key]=engine.tasks[key]
 recovery.RECOVERY=out;done={c for c in protocol['versions'] if all((a.method,c,i) in engine.results for i in range(20))};start=time.monotonic();maximum=8*3600
 summary=summarize(protocol,list(engine.results.values()),engine.universes,1)
 while len(done)<len(protocol['versions']):
  if time.monotonic()-start>maximum:raise RuntimeError('inventory wait/execution stage budget exceeded')
  try:inventory=json.loads(a.inventory.read_text()) if a.inventory.exists() else []
  except json.JSONDecodeError:time.sleep(1);continue
  pending=[r for r in inventory if r['crate'] not in done and r['crate'] in protocol['versions']]
  if not pending:time.sleep(10);continue
  for row in pending:
   c=row['crate']
   if c not in protocol['versions'] or row['version']!=protocol['versions'][c]:raise ValueError('native inventory version mismatch')
   recovery.health_gate();save(out/(c+'-native-evidence.json'),row)
   for item in row['targets']:
    i=item['slot']
    if (a.method,c,i) in engine.tasks:continue
    if 'source' in item:engine.admit(a.method,c,i,Path(item['source']),baseline=True)
    else:engine.failure(a.method,c,i,item['status'],'native inventory has no admitted source for this fixed slot',item)
   states={(a.method,c,i):(engine.results[(a.method,c,i)]['status'] if (a.method,c,i) in engine.results else ('ready_not_started' if engine.tasks[(a.method,c,i)]['status']=='ready' else engine.tasks[(a.method,c,i)]['status'])) for i in range(20)}
   recovery.phase_b(out,protocol,states,8)
   for i in range(20):
    result=out/'round-01'/a.method/c/str(i)/'result.json'
    if result.exists():engine.results[(a.method,c,i)]=json.loads(result.read_text())
   done.add(c)
   summary=summarize(protocol,list(engine.results.values()),engine.universes,1);summary['track']={'method':a.method,'completed_crates':sorted(done),'original_graft_batch_unchanged':True}
   save(out/'partial-summary.json',summary)
 engine.fuzz_pool.shutdown(wait=True);save(out/'summary.json',summary);print(json.dumps({'method':a.method,'status':summary['status'],'completed_crates':len(done)}))
if __name__=='__main__':main()
