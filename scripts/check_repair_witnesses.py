#!/usr/bin/env python3
"""Hand-authored diagnostic witnesses, never counted as LLM repair success."""
import argparse,json,re,shutil
from pathlib import Path
from unittest.mock import patch
from admission_diagnostics import atomic_json,fingerprint
from launch_controlled_round import Engine,ROOT
from model_source import extract

FAILED=[('bytes',10),('time',0),('time',1),('time',12),('fixedbitset',0),('fixedbitset',4),('fixedbitset',14),('csv',0),('csv',11),('bumpalo',0)]

def witness(batch,crate,slot):
    d=Path(batch)/'preparation/round-01/GRAFT'/crate/str(slot)
    source=(d/'attempt-0.rs').read_text();plan=d/'plan.json'
    if crate=='bytes':
        plan=d/'replanning/candidate-1/plan.json'
        source='''extern crate alloc;
pub fn graft_body(data:&[u8])->Vec<(usize,Vec<u8>)> {
 let mut trace=Vec::new();
 let mut v0=<bytes::BytesMut>::new();
 v0.extend_from_slice(&data[..data.len().min(64)]);
 let mut v1=<bytes::BytesMut>::len(&v0);
 trace
}'''
    elif crate=='time':
        source=source.replace('let mut v2 = <time::Date>::from_ordinal_date(v0, v1);',
            'let b=data.first().copied().unwrap_or(0); v0=2000+i32::from(b%100); v1=1+u16::from(b);\nlet mut v2 = <time::Date>::from_ordinal_date(v0, v1);')
        source=source.replace('let mut v5 = <time::Time>::from_hms(v4, v4, v4);',
            'v4=b%24; let mut v5 = <time::Time>::from_hms(v4, v4, v4);')
        source=source.replace('let mut v9 = <time::UtcOffset>::from_hms(v8, v8, v8);',
            'v8=(b%24) as i8; let mut v9 = <time::UtcOffset>::from_hms(v8, v8, v8);')
    elif crate=='fixedbitset' and slot==0:
        source=source.replace('data[0]','data.first().copied().unwrap_or(0)')
    elif crate=='fixedbitset' and slot==4:
        source=source.replace('let mut v4 = <fixedbitset::FixedBitSet>::set(&mut v0, v1 % v0.len(), v2);',
            'let index=v1 % v0.len(); let mut v4 = <fixedbitset::FixedBitSet>::set(&mut v0, index, v2);')
    elif crate=='fixedbitset':
        source=source.replace('let mut v0 = {\n        <fixedbitset::FixedBitSet>::grow(&mut v0, grow_size);\n        v0\n    };',
            '<fixedbitset::FixedBitSet>::grow(&mut v0, grow_size);')
    elif crate=='csv' and slot==0:
        source=source.replace('let mut v2 = <csv::Position>::new();',
            'let mut v2 = <csv::Position>::new(); v2.set_byte(u64::from(data.first().copied().unwrap_or(0)));')
    elif crate=='csv':
        source=source.replace('let mut v0 = <csv::StringRecord>::new();',
            'let mut v0 = <csv::StringRecord>::new(); v0.push_field(core::str::from_utf8(&data[..data.len().min(64)]).unwrap_or(""));')
    elif crate=='bumpalo':
        source=source.replace('let mut v0 = <bumpalo::Bump>::new();',
            'let mut v0 = <bumpalo::Bump>::new(); if !data.is_empty(){v0.alloc_slice_copy(&data[..data.len().min(64)]);}')
    return source,plan


def check(batch,output,planner,harness,profile):
    batch,output=Path(batch).resolve(),Path(output).resolve()
    if output.is_relative_to(batch):raise ValueError('Cannot overwrite evidence')
    output.mkdir(parents=True,exist_ok=False);(output/'runtime').symlink_to(batch/'runtime',target_is_directory=True)
    tools=output/'tools';tools.mkdir()
    for name,path in [('graft-plan',planner),('graft-harness',harness)]:shutil.copy2(path,tools/name)
    cases=[]
    for crate,slot in FAILED:
        source,plan=witness(batch,crate,slot)
        cases.append((crate,slot,'manual-positive',source,plan,True))
    # All are negative witnesses: actual API arguments remain constant or bypassed.
    csv0,plan=witness(batch,'csv',0)
    constant=(batch/'preparation/round-01/GRAFT/csv/0/attempt-0.rs').read_text().replace('let mut trace = Vec::new();','let _unused=data.len(); let mut trace=Vec::new();')
    cases.append(('csv',0,'unused-data-negative',constant,plan,False))
    bump,plan=witness(batch,'bumpalo',0)
    constant=(batch/'preparation/round-01/GRAFT/bumpalo/0/attempt-0.rs').read_text().replace('Vec::new()));','data.to_vec()));')
    cases.append(('bumpalo',0,'trace-only-negative',constant,plan,False))
    source,plan=witness(batch,'fixedbitset',14)
    cases.append(('fixedbitset',14,'deleted-target-negative',source.replace('<fixedbitset::FixedBitSet>::toggle(&mut v0, index)','()'),plan,False))
    cases.append(('fixedbitset',14,'forged-marker-negative',source.replace('let mut trace','eprintln!("GRAFT_CALL:1:(1,)"); let mut trace'),plan,False))
    rows=[]
    def forbidden(*a,**k):raise AssertionError('No model requests in manual witness check')
    with patch('launch_controlled_round.costed_send',forbidden),patch('urllib.request.urlopen',forbidden):
        engine=Engine(output,cost_policy=ROOT/'experiments/cost-control/policy.json',admission_profile=profile,
            planner_bin=tools/'graft-plan',harness_bin=tools/'graft-harness',admission_only=True)
        engine.request=forbidden
        try:
            for crate,slot,name,source,plan,expected in cases:
                work=output/'cases'/crate/str(slot)/name;work.mkdir(parents=True)
                engine.directory=lambda *args,work=work:work
                key=('GRAFT',crate,slot);engine.tasks.pop(key,None);engine.results.pop(key,None)
                (work/'source.rs').write_text(source);shutil.copyfile(plan,work/'plan.json')
                accepted=bool(engine.admit(*key,work/'source.rs',plan=work/'plan.json'))
                rows.append({'crate':crate,'slot':slot,'kind':name,'expected_accepted':expected,'accepted':accepted,
                    'source':fingerprint(work/'source.rs'),'plan':fingerprint(plan),'work':str(work)})
                atomic_json(output/'progress.json',{'completed':len(rows),'cases':rows})
        finally:engine.fuzz_pool.shutdown(wait=True)
    result={'schema':'graft-manual-repair-witness-check-v1','new_model_requests':0,'fuzz_jobs':0,
        'manual_positives':sum(r['expected_accepted'] for r in rows),
        'manual_positives_passed':sum(r['accepted'] and r['expected_accepted'] for r in rows),
        'negatives':sum(not r['expected_accepted'] for r in rows),
        'negative_false_accepts':sum(r['accepted'] and not r['expected_accepted'] for r in rows),
        'scope':'Manually edited engineering witnesses, not LLM repair success or formal fuzz data','rows':rows}
    atomic_json(output/'summary.json',result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['batch','output','planner','harness','profile']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=check(a.batch,a.output,a.planner,a.harness,a.profile)
    print(json.dumps({k:v for k,v in r.items() if k!='rows'}))
