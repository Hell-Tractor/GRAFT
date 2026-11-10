#!/usr/bin/env python3
"""Review the frozen v4 calibration gates; never launches experiments."""
import argparse,json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from admission_diagnostics import atomic_json,fingerprint
from model_cost_control import read_ledger,spent
from generation_metrics import distribution


def timing_metrics(records):
    attempts=[a for record in records for a in record['attempts']]
    def durations(attempt):
        return [c.get('seconds',c.get('elapsed_seconds')) for c in attempt.get('compiler_runs',[])]
    return {'model_call_seconds':distribution([a.get('model_seconds') for a in attempts]),
        'compiler_process_seconds':distribution([v for a in attempts for v in durations(a)]),
        'attempt_compiler_total_seconds':distribution([sum(v for v in durations(a) if v is not None)
            for a in attempts if durations(a)]),
        'attempt_validation_seconds':distribution([a.get('compile_validation_seconds') for a in attempts]),
        'generation_job_seconds':distribution([r.get('elapsed_seconds') for r in records])}


def review(batch):
    batch=Path(batch);summary=json.loads((batch/'calibration-summary.json').read_text())
    manifest=json.loads((batch/'calibration-manifest.json').read_text());authority=read_ledger(batch/'batch-model-cost.json')
    targets=[];attempts=[];records=[];influence=Counter();evidence_ok=True;no_bypass=True
    for case,row in zip(manifest['cases'],summary['results']):
        if (case['crate'],case['slot'])!=(row['crate'],row['slot']):raise ValueError('Frozen target order changed')
        accepted=bool(row['accepted']);record={};diagnosis=None
        path=row.get('generation_record')
        if path:
            if fingerprint(path['path'])!=path:raise ValueError('Generation evidence changed')
            record=json.loads(Path(path['path']).read_text());records.append(record);attempts+=record['attempts']
            if accepted:
                a=next(a for a in record['attempts'] if a.get('accepted'))
                d=Path(path['path']).parent
                plan=json.loads(Path(a['plan']['path']).read_text())['plan']
                if fingerprint(a['plan']['path'])!=a['plan']:raise ValueError('Accepted plan changed')
                original=json.loads(Path(case['original_plan']['path']).read_text())
                review=json.loads((d/'validity.json').read_text());receipt=review.get('repair_contract',{})
                probes=[json.loads((d/p).read_text()) for p in ['original-wrapper-probe.json','wrapper-probe.json']]
                state=probes[0].get('argument_input_influence');influence[state]+=1
                target_call=next(s for s in reversed(plan['steps']) if s['kind']=='call' and s['api']==plan['target'])
                zero_args=len(target_call['args'])==0
                evidence_ok &= all(p.get('bounded_probe_passed') and p.get('extended_probe_passed') and p.get('reached_inputs',0)>0 for p in probes)
                evidence_ok &= state==('not_applicable_no_arguments' if zero_args else 'observed_repeatable')
                no_bypass &= (review.get('accepted') is True and review.get('unsafe_blocks')==0 and review.get('raw_pointer_casts')==0
                    and any(c['api']==plan['target'] for c in receipt.get('calls',[]))
                    and receipt.get('source_sha256')==fingerprint(d/f"attempt-{a['index']}.rs")['sha256']
                    and all(plan[k]==original[k] for k in ['target','crate_name','crate_version','rustdoc_sha256']))
                if zero_args:
                    no_bypass &= (receipt.get('target_signature') or {}).get('argument_count')==0 and receipt.get('input_influence_applicability')=='not_applicable_no_arguments'
            elif record.get('attempts'):
                diagnosis=record['attempts'][-1].get('validation_evidence',{}).get('diagnosis')
        targets.append({'crate':case['crate'],'slot':case['slot'],'prior_accepted':case['prior_accepted'],
            'accepted':accepted,'status':row['status'],'attempts':row['attempts'],'paid_or_reserved_calls':row.get('paid_or_reserved_calls',0),
            'spent_or_reserved_cny':row['spent_or_reserved_cny'],'final_diagnosis':diagnosis})
    if len(targets)!=len(manifest['cases']) or len(summary['results'])!=len(targets):raise ValueError('Missing frozen target slots')
    controls=[r for r in targets if r['prior_accepted']];accepted=sum(r['accepted'] for r in targets)
    unknown=sum(e['status']!='usage_reported' for e in authority['entries']);total=spent(authority)
    ratio=total/accepted if accepted else None
    current_calls=sum(len(r['attempts']) for r in records)
    gates=[
        {'id':1,'name':'all 24 targets completed without infrastructure gaps',
         'passed':len(targets)==24 and summary['status']=='calibration_complete' and all(r.get('finished') for r in records) and len(records)==24
                  and not any(r['status'] in ['infrastructure_failed','generation_timeout','not_run_batch_stopped'] for r in targets)},
        {'id':2,'name':'at least 17/24 and all five original-success controls retained',
         'passed':accepted>=17 and len(controls)==5 and all(r['accepted'] for r in controls)},
        {'id':3,'name':'actual target execution and applicable repeatable argument influence','passed':bool(evidence_ok)},
        {'id':4,'name':'batch/target/call limits and cost per admitted target',
         'passed':total<=Decimal(manifest['batch_budget_cny']) and unknown==0 and not any(e.get('reservation_exceeded') for e in authority['entries'])
                  and all(Decimal(r['spent_or_reserved_cny'])<=Decimal('.24') and r['paid_or_reserved_calls']<=2 for r in targets)
                  and ratio is not None and ratio<Decimal('.25')},
        {'id':5,'name':'no target deletion, argument substitution, unsafe bypass or version drift','passed':bool(no_bypass)}]
    result={'schema':'graft-v4-calibration-gate-review-v1','overall_pass':all(g['passed'] for g in gates),
        'status':'gate_passed' if all(g['passed'] for g in gates) else 'gate_failed','auto_expand':False,
        'counts':{'cases':len(targets),'accepted':accepted,'admission_rate':accepted/len(targets),
            'first_response_compile_success':sum(bool(r['attempts'][0].get('normalized_coverage_compile_success')) for r in records if r['attempts']),
            'accepted_first_attempt':sum(bool(a.get('accepted')) and a['index']==0 for a in attempts),
            'repair_success':sum(bool(a.get('accepted')) and a['index']>0 for a in attempts),
            'replanning_targets':sum(bool(r.get('plan_lineage')) for r in records),
            'duplicate_paid_token_responses':sum(bool(a.get('paid_response_same_ast')) for a in attempts),
            'original_success_controls_retained':sum(r['accepted'] for r in controls),'controls':len(controls),
            'paid_or_reserved_calls':len(authority['entries']),'recorded_attempts':current_calls},
        'cost':{'current_cny':str(total),'current_cost_per_admitted_cny':str(ratio) if ratio is not None else None,
            'batch_cap_cny':manifest['batch_budget_cny'],'unknown_reserved_calls':unknown,
            'listed_prior_calibrations_cny':summary['prior_calibration_spent_or_reserved_cny'],
            'cumulative_listed_cny':summary['cumulative_listed_calibrations_spent_or_reserved_cny'],
            'cumulative_listed_cost_per_current_admitted_cny':summary['cumulative_listed_cost_per_current_admitted_cny']},
        'review_implementation':fingerprint(__file__),
        'timing':timing_metrics(records),
        'influence_counts':dict(influence),'final_failure_causes':dict(Counter(r['final_diagnosis']['code'] if r['final_diagnosis'] else r['status'] for r in targets if not r['accepted'])),
        'gates':gates,'controls':controls,'targets':targets,
        'scope':'New targeted variant on a development-used cohort, not an unbiased full generation rate; no formal fuzz or automatic expansion.'}
    atomic_json(batch/'calibration-gate-review.json',result)
    lines=['# v4 calibration gate review','',f"Status: {result['status']}; admission: {accepted}/{len(targets)}; controls: {sum(r['accepted'] for r in controls)}/{len(controls)}.",
        f"Current cost: CNY {total}; cost/admitted: {ratio}; unknown reservations: {unknown}.",'']
    lines += [f"- Gate {g['id']}: {'PASS' if g['passed'] else 'FAIL'} — {g['name']}" for g in gates]
    lines+=['','No full fuzz or automatic expansion was launched. See JSON for per-target evidence and cumulative historical cost.']
    (batch/'calibration-gate-review.md').write_text('\n'.join(lines)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);a=p.parse_args()
    r=review(a.batch);print(json.dumps({k:v for k,v in r.items() if k not in ('targets','controls')},ensure_ascii=False))
