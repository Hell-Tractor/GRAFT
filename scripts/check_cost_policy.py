#!/usr/bin/env python3
"""Small, separate paid calibration; never runs or modifies a formal round."""
import argparse
import concurrent.futures as cf
import json
import os
from pathlib import Path
import re
import subprocess
import time

from launch_controlled_round import Engine, ROOT, PLANNER, HARNESS
from model_cost_control import (BudgetRejected, compact_controlled, compact_native,
                               read_ledger, read_policy, spent)
from costed_model_request import send


def main():
    p=argparse.ArgumentParser();p.add_argument('--source-batch',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--policy',type=Path,default=ROOT/'experiments/cost-control/policy.json')
    p.add_argument('--methods',help='comma-separated calibration subset; never resumes or replaces previous paid cases')
    a=p.parse_args();source=a.source_batch.resolve();out=a.output.resolve()
    if out.exists():raise SystemExit('use a new calibration directory; do not overwrite paid records')
    out.mkdir(parents=True);(out/'runtime').symlink_to(source/'runtime',target_is_directory=True)
    policy=read_policy(a.policy);engine=Engine(out,cost_policy=a.policy)
    cases=[]
    for c,slot in [('base64',0),('base64',1),('bumpalo',0)]:
        prep=source/'preparation/round-01/GRAFT'/c/str(slot)
        catalog=json.loads((source/'plans/round-01'/c/'catalog.json').read_text())
        raw=next((source/'runtime/raw-rustdoc').glob(c+'-*.json'));index=json.loads(raw.read_text())['index']
        docs=[{**api,'documentation':index.get(api['id'],{}).get('docs','')} for api in catalog['apis']]
        selection=json.loads((source/'plans/round-01'/c/'selection.json').read_text())['Full'][slot]
        plan=source/'plans/round-01'/c/(selection+'.json')
        prompt='Use only the pinned '+c+' default-feature library. Public documentation pool:\n'+json.dumps(docs,ensure_ascii=False)+'\n'+(prep/'core-prompt.txt').read_text()
        cases.append({'name':'GRAFT-'+c+'-'+str(slot),'method':'GRAFT','crate':c,'plan':plan,
                      'messages':[{'role':'user','content':compact_controlled(prompt,policy)}]})
    cases.append({'name':'LLM-only-ryu','method':'LLM-only','crate':'ryu','messages':[{'role':'user','content':'Use ryu 1.0.20 default features. Return Rust source only defining pub fn fuzz_target(data: &[u8]). Exercise safe public ryu API calls with arguments derived from data. Fully qualify library calls. Preserve outputs with std::hint::black_box. No main, unsafe, AFL, process, environment, filesystem or network access. Avoid unbounded allocation.'}]})
    for c in ['bytes','byteorder']:
        request=next((source/'deepsurf-native/round-01'/c/'llm-calls').glob('*-request.json'))
        body=json.loads(request.read_text())
        # Rebuild the public-doc pool as in the new native preparation adapter;
        # the historical 32k-character slice may be invalid JSON.
        universe=json.loads((source/'runtime/universes/api_universes'/(c+'.json')).read_text())
        raw=json.loads(next((source/'runtime/raw-rustdoc').glob(c+'-*.json')).read_text())
        docs=[{'path':row.get('name'),'documentation':raw['index'].get(row['rustdoc_id'],{}).get('docs','')} for row in universe['apis'] if row.get('public_name_body_definition')]
        for message in body['messages']:
            if message['role']=='user':
                message['content']=re.sub(r'=== DOCUMENTATION START ===.*?=== DOCUMENTATION END ===',lambda m:'=== DOCUMENTATION START ===\n'+json.dumps(docs,ensure_ascii=False)+'\n=== DOCUMENTATION END ===',message['content'],flags=re.S)
        cases.append({'name':'deepSURF-'+c,'method':'deepSURF','crate':c,'native':True,'messages':compact_native(body['messages'],policy)})

    def run(case):
        directory=out/'cases'/case['name'];directory.mkdir(parents=True)
        began=time.monotonic();messages=case['messages'];answer=None
        result={'case':case['name'],'method':case['method'],'crate':case['crate'],'compiled':False,'validated':False,'attempts':[]}
        try:
            for index in range(policy['max_calls_per_target']):
                raw=send(messages,policy,directory/'cost.json',out/'crate-cost'/case['method']/(case['crate']+'.json'),directory/f'request-{index}.json',directory/f'response-{index}.json')
                choice=raw['choices'][0];code=(choice['message'].get('content') or '').strip()
                result['attempts'].append({'usage':raw.get('usage'),'finish_reason':choice.get('finish_reason')})
                if not code or choice.get('finish_reason')!='stop':break
                if code.startswith('```'):code=code.split('\n',1)[1].rsplit('```',1)[0]
                answer=directory/f'answer-{index}.rs';answer.write_text(code)
                candidate=answer
                if case.get('native'):
                    candidate=directory/f'assembled-{index}.rs'
                    command=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
                        '--mount',f'type=bind,src={source}/deepsurf-port,dst=/native,readonly',
                        '--mount',f'type=bind,src={directory},dst=/case,readonly',
                        '-e','GRAFT_POSTPROCESS_RESPONSE=/case/'+answer.name,
                        '--entrypoint','/native/build/release/harness_generator','deepsurf:graft-ready']
                    with candidate.open('wb') as target:
                        assembled=subprocess.run(command,stdout=target,stderr=subprocess.PIPE,timeout=30)
                    if assembled.returncode:raise RuntimeError('native offline assembly failed: '+assembled.stderr.decode(errors='replace')[-1000:])
                normalized=directory/'normalized.rs';command=[str(HARNESS),str(candidate),str(normalized),str(directory/'validity.json')]
                if case.get('plan'):command+=['--plan',str(case['plan'])]
                if case.get('native'):command+=['--baseline']
                norm=subprocess.run(command,capture_output=True,text=True,timeout=30)
                diagnostics=norm.stderr
                if norm.returncode==0:
                    compiled=engine.compile(normalized,directory/'replay','coverage')
                    result['compiled']=compiled['returncode']==0
                    diagnostics=(directory/'replay.compile.log').read_text(errors='replace')
                    if result['compiled']:
                        if case.get('plan'):
                            original=engine.compile(candidate,directory/'original','coverage')
                            probe=subprocess.run(['prlimit','--as=1073741824','--',str(PLANNER),'probe',str(case['plan']),str(directory/'original'),str(directory/'original-probe.json')],env=dict(engine.env,LLVM_PROFILE_FILE='/dev/null'),capture_output=True,text=True,timeout=20) if original['returncode']==0 else None
                            result['validated']=probe is not None and probe.returncode==0
                            diagnostics=probe.stderr if probe is not None else 'original wrapper did not compile'
                        else:
                            probe_input=directory/'input';probe_input.write_bytes(bytes(64))
                            probe=subprocess.run(['prlimit','--as=1073741824','--',str(directory/'replay'),str(probe_input)],env=dict(engine.env,LLVM_PROFILE_FILE='/dev/null'),capture_output=True,text=True,timeout=5)
                            result['validated']=probe.returncode==0
                            diagnostics=probe.stderr
                        if result['validated']:break
                # One latest source/error pair; no recursively repeated history.
                if case.get('native'):
                    messages=[dict(m) for m in case['messages']]
                    for m in messages:
                        if m['role']=='user':
                            m['content']=re.sub(r'=== SAMPLE HARNESS START ===.*?=== SAMPLE HARNESS END ===',lambda _: '=== SAMPLE HARNESS START ===\n'+code+'\n=== SAMPLE HARNESS END ===',m['content'],flags=re.S)
                            m['content']+='\n=== ERROR MESSAGE START ===\n'+diagnostics+'\n=== ERROR MESSAGE END ==='
                    messages=compact_native(messages,policy)
                else:
                    text=case['messages'][0]['content']+'\nCURRENT SOURCE:\n'+code+'\nDIAGNOSTICS:\n'+diagnostics
                    messages=[{'role':'user','content':compact_controlled(text,policy)}]
        except Exception as error:result['error']=type(error).__name__+': '+str(error)
        result.update(elapsed_seconds=time.monotonic()-began,peak_estimate_or_reserved_cny=str(spent(read_ledger(directory/'cost.json'))))
        (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n');return result
    if a.methods:cases=[c for c in cases if c['method'] in set(a.methods.split(','))]
    if not cases:raise ValueError('no calibration cases selected')
    with cf.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,cases))
    engine.fuzz_pool.shutdown()
    from decimal import Decimal
    cost=sum(Decimal(r['peak_estimate_or_reserved_cny']) for r in results)
    good=sum(r['validated'] for r in results)
    report={'scope':'Separate six-case cost calibration. Compilation and bounded probes only; no AFL, coverage or formal round data. Case selection is diagnostic, not an unbiased success-rate estimate.',
        'policy':policy,'cases':results,'total_peak_estimate_or_reserved_cny':str(cost),
        'mean_cny_per_attempted_case':str(cost/len(results)),
        'cny_per_validated_case_including_failed_costs':str(cost/good) if good else None,
        'compiled_cases':sum(r['compiled'] for r in results),'bounded_probe_passed_cases':good}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('policy','cases')},indent=2))
    progress=json.loads((out/'progress.json').read_text());progress.update(status='calibration_finished',formal_round_complete=False,formal_round_started=False)
    (out/'progress.json').write_text(json.dumps(progress,indent=2)+'\n')


if __name__=='__main__':main()
