#!/usr/bin/env python3
"""Pair every saved response with its actual frozen plan; no paid requests."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from admission_diagnostics import atomic_json, fingerprint
from launch_controlled_round import Engine, ROOT
from model_source import extract
from repair_context import classification

IDENTITY=('target','crate_name','crate_version','package_name','features','default_features','rustdoc_sha256')


def collect(batch):
    batch=Path(batch).resolve();items=[]
    for path in sorted((batch/'preparation/round-01/GRAFT').glob('*/*/generation.json')):
        record=json.loads(path.read_text());directory=path.parent
        initial_path=directory/'plan.json';initial=json.loads(initial_path.read_text())['plan']
        if not record.get('finished'):raise ValueError('Incomplete saved target')
        for attempt in record['attempts']:
            index=attempt['index'];binding=attempt.get('plan')
            if binding:
                if fingerprint(binding['path'])!=binding:raise ValueError('Changed attempt plan')
                plan=Path(binding['path'])
            elif index==0:plan=initial_path
            else:raise ValueError('Missing actual plan binding for repair attempt')
            current=json.loads(plan.read_text())['plan']
            if any(current.get(k)!=initial.get(k) for k in IDENTITY):raise ValueError('Changed pinned target identity')
            response=directory/f'attempt-{index}.rs'
            sealed=attempt.get('validation_evidence',{}).get('source')
            if sealed and fingerprint(response)!=sealed:raise ValueError('Changed sealed response')
            raw=directory/f'raw-response-{index}.json'
            if raw.exists():
                content=json.loads(raw.read_text())['choices'][0]['message']['content']
            else:content=response.read_text()
            items.append({'crate':record['crate'],'slot':record['slot'],'index':index,
                'plan':fingerprint(plan),'saved_source':fingerprint(response),
                'raw':fingerprint(raw) if raw.exists() else None,
                'generation':fingerprint(path),'content':content,
                'request':fingerprint(directory/f'request-{index}.json') if (directory/f'request-{index}.json').exists() else None,
                'ledger':fingerprint(directory/'model-cost.json') if (directory/'model-cost.json').exists() else None,
                'prior_accepted':bool(attempt.get('accepted'))})
    if not items:raise ValueError('No completed responses')
    return items


def replay(batch,output,planner,harness,profile):
    batch,output=Path(batch).resolve(),Path(output).resolve()
    if output.is_relative_to(batch):raise ValueError('Cannot overwrite source batch')
    items=collect(batch);output.mkdir(parents=True,exist_ok=False)
    (output/'runtime').symlink_to(batch/'runtime',target_is_directory=True)
    tools=output/'tools';tools.mkdir()
    for name,path in [('graft-plan',planner),('graft-harness',harness)]:shutil.copy2(path,tools/name)
    atomic_json(output/'input-manifest.json',{'items':[{k:v for k,v in row.items() if k!='content'} for row in items],
        'profile':fingerprint(profile),'scope':'All saved attempts; original ordering, not best-candidate selection'})
    rows=[]
    def forbidden(*a,**k):raise AssertionError('Offline replay cannot send model requests')
    # Both orchestration and the actual network transport are disabled.
    with patch('launch_controlled_round.costed_send',forbidden),patch('urllib.request.urlopen',forbidden):
        engine=Engine(output,cost_policy=ROOT/'experiments/cost-control/policy.json',
            admission_profile=profile,planner_bin=tools/'graft-plan',harness_bin=tools/'graft-harness',admission_only=True)
        engine.request=forbidden
        try:
            for item in items:
                work=output/'attempts'/item['crate']/str(item['slot'])/str(item['index']);work.mkdir(parents=True)
                engine.directory=lambda *args,work=work:work
                key=('GRAFT',item['crate'],item['slot'])
                engine.tasks.pop(key,None);engine.results.pop(key,None)
                shutil.copyfile(item['plan']['path'],work/'plan.json')
                p=json.loads((work/'plan.json').read_text())['plan']
                raw_path=engine.runtime/'raw-rustdoc'/(p['crate_name']+'-'+p['crate_version']+'.json')
                if fingerprint(raw_path)['sha256']!=p['rustdoc_sha256']:raise ValueError('Changed raw signature source')
                atomic_json(work/'input-applicability.json',classification(p,json.loads(raw_path.read_text())))
                (work/'raw.txt').write_text(item['content'])
                parsed=subprocess.run([str(tools/'graft-plan'),'source-key',str(work/'raw.txt'),str(work/'raw-key.json')],
                                      capture_output=True,timeout=5)
                try:source,receipt=extract(item['content'],validated_plain=parsed.returncode==0)
                except ValueError as error:source=item['content'];receipt={'accepted':False,'reason':str(error)}
                (work/'source.rs').write_text(source);atomic_json(work/'extraction.json',receipt)
                accepted=bool(engine.admit(*key,work/'source.rs',plan=work/'plan.json'))
                row={k:v for k,v in item.items() if k!='content'}
                row.update(accepted=accepted,work=str(work),extraction=receipt)
                rows.append(row);atomic_json(output/'progress.json',{'attempts_complete':len(rows),'attempts_total':len(items),'new_model_requests':0,'fuzz_jobs':0})
        finally:engine.fuzz_pool.shutdown(wait=True)
    targets={}
    for row in rows:
        key=f"{row['crate']}/{row['slot']}"
        target=targets.setdefault(key,{'accepted':False,'prior_accepted':False,'attempts':[]})
        target['accepted']|=row['accepted'];target['prior_accepted']|=row['prior_accepted']
        target['attempts'].append({'index':row['index'],'accepted':row['accepted']})
    result={'schema':'graft-all-attempt-offline-replay-v1','attempts':len(rows),'targets':len(targets),
        'admitted_targets':sum(t['accepted'] for t in targets.values()),
        'new_model_requests':engine.requests,'fuzz_jobs':len(engine.fuzz_futures),'rows':rows,'target_results':targets,
        'scope':'Saved responses reprocessed under a new implementation; not new-generation efficacy or formal coverage'}
    atomic_json(output/'summary.json',result);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['batch','output','planner','harness','profile']:parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args();r=replay(a.batch,a.output,a.planner,a.harness,a.profile)
    print(json.dumps({k:v for k,v in r.items() if k not in ('rows','target_results')}))
