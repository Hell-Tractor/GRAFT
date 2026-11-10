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


DEFAULT_CASES = [('bytes', 0), ('chrono', 0), ('fixedbitset', 4), ('time', 0)]


def calibrate(source, output, planner, harness, execute=False, cases=None):
    source, output = Path(source).resolve(), Path(output).resolve()
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
        if not old.get('finished') or old.get('accepted'):
            raise ValueError(f'{crate}:{slot} is not a completed failed control case')
        snapshots.append({'crate': crate, 'slot': slot, 'original_generation': fingerprint(generation),
                          'original_plan': fingerprint(plan), 'prior_status': old['status']})
    atomic_json(output / 'calibration-manifest.json', {
        'scope': 'Independent new-protocol variant, paired to original targets. Not a continuation '
                 'or budget reset of old paid attempts. Admission only; no formal fuzz result.',
        'source_batch': str(source), 'cases': snapshots,
        'target_budget_cny': '0.24', 'max_model_calls_per_target': 2,
        'max_calibration_budget_cny': str(Decimal('.24') * len(cases)),
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
                     'src/bin/graft-plan.rs','src/bin/graft-harness.rs',
                     'experiments/admission-v2/parameter-repair.json','experiments/cost-control/policy.json'):
        destination=implementation/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,destination);captured[relative]=fingerprint(destination)
    atomic_json(implementation/'manifest.json',{'timing':'before model calls','files':captured})
    engine = Engine(output, cost_policy=ROOT / 'experiments/cost-control/policy.json',
                    admission_profile=ROOT / 'experiments/admission-v2/parameter-repair.json',
                    planner_bin=tools/'graft-plan', harness_bin=tools/'graft-harness', admission_only=True)
    results = []
    try:
        for case in snapshots:
            crate, slot = case['crate'], case['slot']
            directory = engine.directory('GRAFT', crate, slot)
            raw_plan = json.loads(Path(case['original_plan']['path']).read_text())
            plan = directory / 'plan.json'
            atomic_json(plan, {'status': 'planned', 'plan': raw_plan})
            raw = json.loads((source / 'runtime/raw-rustdoc' / (crate + '-' + raw_plan['crate_version'] + '.json')).read_text())
            ids = {step['api'] for step in raw_plan['steps'] if step['kind'] == 'call'}
            catalog = json.loads((source / 'plans/round-01' / crate / 'catalog.json').read_text())
            helper_names={'grow','len'} if (crate,raw_plan['crate_version'])==('fixedbitset','0.5.7') else set()
            docs = [{**api, 'documentation': raw['index'].get(api['id'], {}).get('docs', ''),
                     'qualified_helper':(api.get('owner') or {}).get('path','')+'::'+api['path']}
                    for api in catalog['apis'] if api['id'] in ids or api['path'] in helper_names]
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
                            'core_preflight_status': record.get('core_preflight', {}).get('status'),
                            'priced_usage_estimate_cny': str(sum(costs, Decimal(0))),
                            'usage_unknown_or_reserved_calls':unknown,
                            'spent_or_reserved_cny':str(spent(ledger)),
                            'generation_record': fingerprint(directory / 'generation.json')})
            atomic_json(output / 'calibration-progress.json', {'completed': len(results),
                        'total': len(cases), 'model_requests': engine.requests, 'fuzz_jobs': 0,
                        'results': results})
    finally:
        engine.fuzz_pool.shutdown(wait=True)
    accepted = sum(r['accepted'] for r in results)
    total = sum((Decimal(r['priced_usage_estimate_cny']) for r in results), Decimal(0))
    reserved_total=sum((Decimal(r['spent_or_reserved_cny']) for r in results),Decimal(0))
    summary = {'status': 'calibration_complete', 'cases': len(results), 'accepted': accepted,
               'admission_rate': accepted / len(results) if results else None,
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
    parser.add_argument('--cases',help='Comma-separated distinct original crate:slot identities; at most 24')
    args = parser.parse_args()
    cases=[(c,int(slot)) for c,slot in (item.split(':') for item in args.cases.split(','))] if args.cases else None
    result = calibrate(args.source_batch, args.output, args.planner, args.harness, args.execute,cases)
    print(json.dumps(result, ensure_ascii=False))
