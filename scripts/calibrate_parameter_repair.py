#!/usr/bin/env python3
"""Independent, budgeted LLM parameter-repair calibration; never starts AFL."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import shutil
import time

from admission_diagnostics import atomic_json, fingerprint
from launch_controlled_round import Engine, ROOT
from model_cost_control import read_ledger, spent
from preflight_admission import read_profile
from repair_context import classification, helper_names


DEFAULT_CASES = [('bytes', 0), ('chrono', 0), ('fixedbitset', 4), ('time', 0)]


def calibrate(source, output, planner, harness, execute=False, cases=None,
              admission_profile=None, include_accepted_controls=False, expected_manifest=None,
              batch_budget_cny=None,prior_batches=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    profile_path=Path(admission_profile or ROOT/'experiments/admission-v2/parameter-repair.json').resolve()
    profile=read_profile(profile_path)
    if batch_budget_cny is not None:
        from batch_model_budget import limit_value
        batch_budget_cny=str(limit_value(batch_budget_cny))
    prior=[]
    for batch in prior_batches or []:
        prior_root=Path(batch).resolve();summary_path=prior_root/'calibration-summary.json'
        previous=json.loads(summary_path.read_text())
        ledger_paths=sorted((prior_root/'preparation/round-01/GRAFT').glob('*/*/model-cost.json'))
        if len(ledger_paths)!=previous['cases']:raise ValueError('Historical calibration ledger coverage incomplete')
        ledgers=[read_ledger(p) for p in ledger_paths]
        total=sum((spent(d) for d in ledgers),Decimal(0))
        unknown=sum(e['status']!='usage_reported' for d in ledgers for e in d['entries'])
        if unknown:raise ValueError('Historical calibration has unresolved usage')
        if previous.get('spent_or_reserved_cny') is not None and Decimal(previous['spent_or_reserved_cny'])!=total:
            raise ValueError('Historical summary and ledgers disagree')
        prior.append({'summary':fingerprint(summary_path),'target_ledgers':[fingerprint(p) for p in ledger_paths],
                      'spent_or_reserved_cny':str(total),'usage_unknown_or_reserved_calls':unknown})
    source_files=sorted(set(list((ROOT/'src/checked').glob('*.rs'))+
        [ROOT/'src/bin/graft-plan.rs',ROOT/'src/bin/graft-harness.rs',profile_path,
         ROOT/'experiments/cost-control/policy.json']+
        [ROOT/'scripts'/name for name in ('launch_controlled_round.py','parameter_repair.py',
         'preflight_admission.py','admission_diagnostics.py','bounded_replanning.py',
         'model_cost_control.py','costed_model_request.py','calibrate_parameter_repair.py')]+
        [ROOT/'scripts'/name for name in ('model_source.py','repair_context.py','generation_metrics.py','run_experiment_rounds.py')]+
        [ROOT/'scripts/batch_model_budget.py',ROOT/'scripts/review_repair_calibration.py']+
        ([ROOT/profile['semantics_rules']] if profile.get('semantics_rules') else [])))
    implementation_hashes={str(p.relative_to(ROOT)):fingerprint(p) for p in source_files}
    binaries={name:fingerprint(path) if Path(path).is_file() else None
              for name,path in [('planner',planner),('harness',harness)]}
    if output.is_relative_to(source):
        raise ValueError('Calibration output must be outside the original batch')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'runtime').symlink_to(source / 'runtime', target_is_directory=True)
    cases = list(cases if cases is not None else DEFAULT_CASES)
    if not cases or len(cases) > 24 or len(cases) != len(set(cases)):
        raise ValueError('Use 1..24 distinct, preselected original targets')
    snapshots = []
    for crate, slot in cases:
        original = source / 'preparation/round-01/GRAFT' / crate / str(slot)
        generation, plan = original / 'generation.json', original / 'emitted/plan.json'
        old = json.loads(generation.read_text())
        if not old.get('finished') or (old.get('accepted') and not include_accepted_controls):
            raise ValueError(f'{crate}:{slot} is not an allowed completed control case')
        original_plan=json.loads(plan.read_text())
        raw_path=source/'runtime/raw-rustdoc'/(crate+'-'+str(original_plan.get('crate_version'))+'.json')
        applicability=(classification(original_plan,json.loads(raw_path.read_text()) if raw_path.exists() else None)
                       if original_plan.get('steps') else None)
        snapshots.append({'crate': crate, 'slot': slot, 'original_generation': fingerprint(generation),
                          'original_plan': fingerprint(plan), 'prior_status': old['status'],
                          'prior_accepted':bool(old.get('accepted')),'input_applicability':applicability})
    if expected_manifest:
        expected=json.loads(Path(expected_manifest).read_text())
        if (expected['cases']!=snapshots or expected['admission_profile']!=fingerprint(profile_path)
                or expected['source_batch']!=str(source)
                or expected['implementation']!=implementation_hashes or expected['binaries']!=binaries
                or expected.get('batch_budget_cny')!=batch_budget_cny or expected.get('prior_calibrations',[])!=prior):
            raise ValueError('Prepared target selection, source evidence or profile changed')
    atomic_json(output / 'calibration-manifest.json', {
        'scope': 'Independent new-protocol variant, paired to original targets. Not a continuation '
                 'or budget reset of old paid attempts. Admission only; no formal fuzz result.',
        'source_batch': str(source), 'cases': snapshots,
        'admission_profile':fingerprint(profile_path),
        'batch_budget_cny':batch_budget_cny,'prior_calibrations':prior,
        'implementation':implementation_hashes,'binaries':binaries,
        'selection_policy':'Preselected diagnostic strata; original successes and failures retained; no replacement',
        'target_budget_cny': '0.24', 'max_model_calls_per_target': 2,
        'max_calibration_budget_cny': str(min(Decimal('.24')*len(cases),Decimal(batch_budget_cny)) if batch_budget_cny else Decimal('.24')*len(cases)),
        'execute_requested': execute})
    if not execute:
        return {'status': 'prepared_not_executed', 'cases': len(cases), 'model_requests': 0, 'fuzz_jobs': 0}
    tools=output/'tools';tools.mkdir()
    for name,binary in [('graft-plan',planner),('graft-harness',harness)]:
        shutil.copy2(binary,tools/name)
    implementation=output/'implementation';implementation.mkdir()
    captured={}
    for relative in ('scripts/admission_diagnostics.py','scripts/preflight_admission.py',
                     'scripts/launch_controlled_round.py','scripts/generation_metrics.py',
                     'scripts/run_experiment_rounds.py','scripts/parameter_repair.py',
                     'scripts/calibrate_parameter_repair.py','scripts/model_cost_control.py',
                     'scripts/costed_model_request.py','src/checked/repair.rs',
                     'scripts/bounded_replanning.py','src/checked/observe.rs',
                     'src/bin/graft-plan.rs','src/bin/graft-harness.rs',
                     'experiments/admission-v2/parameter-repair.json','experiments/cost-control/policy.json'):
        destination=implementation/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,destination);captured[relative]=fingerprint(destination)
    for source_path in source_files:
        destination=implementation/source_path.relative_to(ROOT)
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source_path,destination)
        captured[str(source_path.relative_to(ROOT))]=fingerprint(destination)
    atomic_json(implementation/'manifest.json',{'timing':'before model calls','files':captured})
    engine = Engine(output, cost_policy=ROOT / 'experiments/cost-control/policy.json',
                    admission_profile=profile_path,
                    planner_bin=tools/'graft-plan', harness_bin=tools/'graft-harness', admission_only=True,
                    batch_budget_cny=batch_budget_cny)
    results = []
    stop_reason=None
    try:
        for case in snapshots:
            crate, slot = case['crate'], case['slot']
            directory = engine.directory('GRAFT', crate, slot)
            raw_plan = json.loads(Path(case['original_plan']['path']).read_text())
            if profile.get('infer_call_results'):raw_plan['infer_call_results']=True
            if case.get('input_applicability',{}):
                raw_plan['target_signature']=case['input_applicability']['signature']
            plan = directory / 'plan.json'
            atomic_json(plan, {'status': 'planned', 'plan': raw_plan})
            raw = json.loads((source / 'runtime/raw-rustdoc' / (crate + '-' + raw_plan['crate_version'] + '.json')).read_text())
            ids = {step['api'] for step in raw_plan['steps'] if step['kind'] == 'call'}
            catalog = json.loads((source / 'plans/round-01' / crate / 'catalog.json').read_text())
            helpers=helper_names(raw_plan)
            docs = [{**api, 'documentation': raw['index'].get(api['id'], {}).get('docs', ''),
                     'qualified_helper':(api.get('owner') or {}).get('path','')+'::'+api['path']}
                    for api in catalog['apis'] if api['id'] in ids or api['path'] in helpers]
            atomic_json(directory/'input-applicability.json',classification(raw_plan,raw))
            initial = ('Use only the pinned ' + crate + ' ' + raw_plan['crate_version'] +
                       ' default-feature library. Public documentation pool:\n' +
                       json.dumps(docs, ensure_ascii=False) + '\nRender the following pinned SequencePlan')
            key = ('GRAFT', crate, slot)
            engine.generation[key] = {'round': 1, 'method': 'GRAFT', 'crate': crate, 'slot': slot,
                'queued_unix_seconds': time.time(), 'attempts': [], 'finished': False, 'accepted': False}
            engine.model('GRAFT', crate, slot, initial, plan=plan)
            record = engine.generation[key]
            costs = [Decimal(a['peak_estimate_cny']) for a in record['attempts'] if 'peak_estimate_cny' in a]
            ledger=read_ledger(directory/'model-cost.json')
            unknown=sum(e.get('status')!='usage_reported' for e in ledger['entries'])
            results.append({'crate': crate, 'slot': slot, 'accepted': record.get('accepted', False),
                            'status': record.get('status'), 'attempts': len(record['attempts']),
                            'paid_or_reserved_calls':len(ledger['entries']),
                            'core_preflight_status': record.get('core_preflight', {}).get('status'),
                            'priced_usage_estimate_cny': str(sum(costs, Decimal(0))),
                            'usage_unknown_or_reserved_calls':unknown,
                            'spent_or_reserved_cny':str(spent(ledger)),
                            'generation_record': fingerprint(directory / 'generation.json')})
            atomic_json(output / 'calibration-progress.json', {'completed': len(results),
                        'total': len(cases), 'model_requests': engine.requests, 'fuzz_jobs': 0,
                        'results': results})
            if batch_budget_cny:
                authority=read_ledger(output/'batch-model-cost.json')
                if unknown or any(e.get('reservation_exceeded') for e in authority['entries']):
                    stop_reason='unknown_usage_or_reservation_violation'
                elif record.get('budget_rejection','').startswith('GRAFT_BATCH_'):
                    stop_reason=record['budget_rejection']
                if stop_reason:break
    finally:
        engine.fuzz_pool.shutdown(wait=True)
    accepted = sum(r['accepted'] for r in results)
    total = sum((Decimal(r['priced_usage_estimate_cny']) for r in results), Decimal(0))
    reserved_total=sum((Decimal(r['spent_or_reserved_cny']) for r in results),Decimal(0))
    executed=len(results)
    for case in snapshots[executed:]:
        results.append({'crate':case['crate'],'slot':case['slot'],'accepted':False,'status':'not_run_batch_stopped',
            'reason':stop_reason,'attempts':0,'priced_usage_estimate_cny':'0','spent_or_reserved_cny':'0',
            'usage_unknown_or_reserved_calls':0,'generation_record':None})
    if batch_budget_cny:
        authority=read_ledger(output/'batch-model-cost.json')
        reserved_total=spent(authority)
    historical=sum((Decimal(p['spent_or_reserved_cny']) for p in prior),Decimal(0))
    summary = {'status': 'calibration_stopped' if stop_reason else 'calibration_complete',
               'stop_reason':stop_reason,'cases':len(snapshots),'executed_cases':executed,'accepted': accepted,
               'admission_rate': accepted / len(snapshots) if snapshots else None,
               'batch_budget_cny':batch_budget_cny,'prior_calibrations':prior,
               'prior_calibration_spent_or_reserved_cny':str(historical),
               'cumulative_listed_calibrations_spent_or_reserved_cny':str(historical+reserved_total),
               'cumulative_listed_cost_per_current_admitted_cny':str((historical+reserved_total)/accepted) if accepted else None,
               'model_requests': engine.requests, 'fuzz_jobs': len(engine.fuzz_futures),
               'usage_priced_estimate_cny': str(total),
               'cost_per_admitted_usage_estimate_cny': str(total / accepted) if accepted else None,
               'spent_or_reserved_cny':str(reserved_total),
               'usage_unknown_or_reserved_calls':sum(r['usage_unknown_or_reserved_calls'] for r in results),
               'billing_note': 'Usage estimates; check persistent ledgers for unknown-spend reservations. '
                               'Includes failed cases; excludes old independent-protocol costs.',
               'results': results, 'formal_round_started': False, 'formal_round_complete': False}
    atomic_json(output / 'calibration-summary.json', summary)
    atomic_json(output / 'progress.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-batch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--planner', type=Path, required=True)
    parser.add_argument('--harness', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help='Send the authorized budgeted model calls')
    parser.add_argument('--admission-profile',type=Path)
    parser.add_argument('--include-accepted-controls',action='store_true')
    parser.add_argument('--case-manifest',type=Path,help='Verify and reuse the prepared target selection')
    parser.add_argument('--batch-budget-cny',help='Atomic shared cap across all pilot targets')
    parser.add_argument('--prior-calibrations',type=Path,nargs='+',help='Prior independent calibration ledgers, recorded without reset')
    parser.add_argument('--cases',help='Comma-separated distinct original crate:slot identities; at most 24')
    args = parser.parse_args()
    cases=[(c,int(slot)) for c,slot in (item.split(':') for item in args.cases.split(','))] if args.cases else None
    if args.case_manifest:
        if cases:parser.error('--cases and --case-manifest are mutually exclusive')
        manifest=json.loads(args.case_manifest.read_text())
        cases=[(c['crate'],c['slot']) for c in manifest['cases']]
    result = calibrate(args.source_batch, args.output, args.planner, args.harness, args.execute,cases,
                       args.admission_profile,args.include_accepted_controls,args.case_manifest,
                       args.batch_budget_cny,args.prior_calibrations)
    print(json.dumps(result, ensure_ascii=False))
