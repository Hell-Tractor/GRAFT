#!/usr/bin/env python3
"""Revalidate already-paid responses under a new call contract, without any HTTP."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import shutil

from admission_diagnostics import atomic_json, fingerprint
from launch_controlled_round import Engine, ROOT
from model_cost_control import read_ledger, spent


def revalidate(batches, output, planner, harness, admission_profile=None):
    batches=[Path(b).resolve() for b in batches];output=Path(output).resolve()
    if any(output.is_relative_to(b) for b in batches):
        raise ValueError('Revalidation must not overwrite a paid calibration')
    cases=[];seen=set()
    for batch in batches:
        for path in sorted((batch/'preparation/round-01/GRAFT').glob('*/*/generation.json')):
            record=json.loads(path.read_text());key=(record['crate'],record['slot'])
            if key in seen:raise ValueError('Duplicate original target in replay')
            seen.add(key)
            if not record.get('finished') or not record.get('attempts'):
                raise ValueError('Only completed paid-response records may be replayed')
            index=next((a['index'] for a in record['attempts'] if a.get('accepted')),
                       record['attempts'][-1]['index'])
            ledger=read_ledger(path.parent/'model-cost.json')
            cases.append({'crate':key[0],'slot':key[1], 'prior_accepted':bool(record.get('accepted')),
                          'generation':fingerprint(path),'plan':fingerprint(path.parent/'plan.json'),
                          'response':fingerprint(path.parent/f'attempt-{index}.rs'),
                          'prior_protocol':fingerprint(batch/'protocol.json'),
                          'prior_spent_or_reserved_cny':str(spent(ledger)),
                          'usage_unknown_or_reserved_calls':sum(e['status']!='usage_reported' for e in ledger['entries'])})
    if not cases:raise ValueError('No saved candidates')
    output.mkdir(parents=True,exist_ok=False)
    (output/'runtime').symlink_to(batches[0]/'runtime',target_is_directory=True)
    tools=output/'tools';tools.mkdir()
    for name,source in [('graft-plan',planner),('graft-harness',harness)]:shutil.copy2(source,tools/name)
    atomic_json(output/'input-manifest.json',{'cases':cases,'selection':
        'Original first admitted response, otherwise the final budgeted response; no new generation or extra candidate search'})
    engine=Engine(output,cost_policy=ROOT/'experiments/cost-control/policy.json',
                  admission_profile=admission_profile or ROOT/'experiments/admission-v2/parameter-repair.json',
                  planner_bin=tools/'graft-plan',harness_bin=tools/'graft-harness',admission_only=True)
    def no_requests(*args,**kwargs):raise AssertionError('Saved-response replay must never call a model')
    engine.request=no_requests
    results=[]
    try:
        for case in cases:
            directory=engine.directory('GRAFT',case['crate'],case['slot'])
            source=directory/'saved-response.rs';plan=directory/'plan.json'
            shutil.copyfile(case['response']['path'],source);shutil.copyfile(case['plan']['path'],plan)
            accepted=bool(engine.admit('GRAFT',case['crate'],case['slot'],source,plan=plan))
            results.append({**case,'admitted_under_current_contract':accepted,
                            'status':engine.identities[('GRAFT',case['crate'],case['slot'])]['status']})
    finally:engine.fuzz_pool.shutdown(wait=True)
    accepted=sum(r['admitted_under_current_contract'] for r in results)
    total=sum((Decimal(r['prior_spent_or_reserved_cny']) for r in results),Decimal(0))
    summary={'schema':'graft-saved-parameter-repair-revalidation-v2','cases':len(results),
        'admitted_saved_candidates':accepted,'prior_admitted_candidates':sum(r['prior_accepted'] for r in results),
        'prior_success_regressions':sum(r['prior_accepted'] and not r['admitted_under_current_contract'] for r in results),
        'new_model_requests':engine.requests,'fuzz_jobs':len(engine.fuzz_futures),
        'prior_generation_spent_or_reserved_cny':str(total),
        'prior_generation_cost_per_current_admitted_candidate_cny':str(total/accepted) if accepted else None,
        'usage_unknown_or_reserved_calls':sum(r['usage_unknown_or_reserved_calls'] for r in results),
        'scope':'Diagnostic saved-response replay; input responses came from distinct prompt/contract revisions. '
                'Not an unbiased new-generation success rate or a formal coverage run.',
        'formal_round_started':False,'formal_round_complete':False,'results':results}
    atomic_json(output/'revalidation-summary.json',summary)
    atomic_json(output/'progress.json',summary)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-batches',required=True,nargs='+',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--planner',required=True,type=Path)
    parser.add_argument('--harness',required=True,type=Path)
    parser.add_argument('--admission-profile',type=Path,
                        default=ROOT/'experiments/admission-v2/parameter-repair.json')
    args=parser.parse_args()
    summary=revalidate(args.input_batches,args.output,args.planner,args.harness,args.admission_profile)
    print(json.dumps({k:v for k,v in summary.items() if k!='results'},ensure_ascii=False))
