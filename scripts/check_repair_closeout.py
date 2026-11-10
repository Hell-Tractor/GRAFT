#!/usr/bin/env python3
"""Offline positive/negative checks on the three final v4 failures; no model calls."""
import argparse,json,shutil
from pathlib import Path
from unittest.mock import patch
from admission_diagnostics import atomic_json,fingerprint
from launch_controlled_round import Engine,ROOT

CASES=[('time',12),('csv',11),('bumpalo',0)]

def witness(crate,source):
    if crate=='time':
        old='let v16: u8 = b1 % 60;'
        if source.count(old)!=1:raise ValueError('Unexpected time scalar fixture')
        return source.replace(old,'let v16: u8 = data.get(1).copied().unwrap_or(b0) % 60;')
    if crate=='csv':
        start=source.index('    let field_len =');end=source.index('    v0.push_field(field_str);',start)
        return source[:start]+('    let field_bytes = &data[..data.len().min(64)];\n'
            '    let field_str = core::str::from_utf8(field_bytes).unwrap_or("");\n')+source[end:]
    return '''extern crate alloc;
pub fn graft_body(data: &[u8]) -> Vec<(usize, Vec<u8>)> {
 let mut trace = Vec::new();
 let mut v0 = <bumpalo::Bump>::new();
 if !data.is_empty() { v0.alloc_slice_copy(&data[..data.len().min(64)]); }
 let mut v1 = <bumpalo::Bump>::allocated_bytes_including_metadata(&v0);
 trace
}'''


def check(batch,output,planner,harness,profile):
    batch,output=Path(batch).resolve(),Path(output).resolve()
    if output.is_relative_to(batch):raise ValueError('Cannot overwrite paid evidence')
    output.mkdir(parents=True,exist_ok=False);(output/'runtime').symlink_to(batch/'runtime',target_is_directory=True)
    tools=output/'tools';tools.mkdir()
    for name,path in [('graft-plan',planner),('graft-harness',harness)]:shutil.copy2(path,tools/name)
    inputs=[]
    for crate,slot in CASES:
        d=batch/'preparation/round-01/GRAFT'/crate/str(slot);g=json.loads((d/'generation.json').read_text())
        if not g['finished'] or g['accepted']:raise ValueError('Expected completed failed target')
        attempt=g['attempts'][-1];path=d/f"attempt-{attempt['index']}.rs"
        if fingerprint(attempt['plan']['path'])!=attempt['plan']:raise ValueError('Changed paid plan')
        inputs.append({'crate':crate,'slot':slot,'source':fingerprint(path),'plan':attempt['plan'],'generation':fingerprint(d/'generation.json')})
    atomic_json(output/'input-manifest.json',{'inputs':inputs,'profile':fingerprint(profile)})
    rows=[]
    def forbidden(*a,**k):raise AssertionError('Offline closeout cannot call a model')
    with patch('launch_controlled_round.costed_send',forbidden),patch('urllib.request.urlopen',forbidden):
        engine=Engine(output,cost_policy=ROOT/'experiments/cost-control/policy.json',admission_profile=profile,
            planner_bin=tools/'graft-plan',harness_bin=tools/'graft-harness',admission_only=True)
        engine.request=forbidden
        try:
            for item in inputs:
                old=Path(item['source']['path']).read_text();crate,slot=item['crate'],item['slot']
                for kind,source,expected in [('saved-failure-negative',old,False),('manual-positive',witness(crate,old),True)]:
                    work=output/'cases'/crate/str(slot)/kind;work.mkdir(parents=True)
                    engine.directory=lambda *args,work=work:work
                    key=('GRAFT',crate,slot);engine.tasks.pop(key,None);engine.results.pop(key,None)
                    (work/'source.rs').write_text(source);shutil.copyfile(item['plan']['path'],work/'plan.json')
                    accepted=bool(engine.admit(*key,work/'source.rs',plan=work/'plan.json'))
                    rows.append({'crate':crate,'slot':slot,'kind':kind,'expected':expected,'accepted':accepted,
                        'source':fingerprint(work/'source.rs'),'plan':item['plan'],'work':str(work)})
        finally:engine.fuzz_pool.shutdown(wait=True)
    result={'schema':'graft-final-repair-witness-v1','new_model_requests':engine.requests,'fuzz_jobs':len(engine.fuzz_futures),
        'manual_positives_passed':sum(r['accepted'] and r['expected']for r in rows),
        'negative_false_accepts':sum(r['accepted'] and not r['expected']for r in rows),
        'all_expectations_met':all(r['expected']==r['accepted']for r in rows),'rows':rows,
        'scope':'Manual engineering witnesses only; not LLM success or formal fuzz results'}
    atomic_json(output/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['batch','output','planner','harness','profile']:p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();r=check(a.batch,a.output,a.planner,a.harness,a.profile)
    print(json.dumps({k:v for k,v in r.items()if k!='rows'}))
