"""Separate admission success, backend availability, and overlapping wall time."""
import statistics
from collections import Counter
from decimal import Decimal


def distribution(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {'n': 0, 'sum_seconds': 0, 'mean_seconds': None, 'median_seconds': None, 'p95_seconds': None}
    import math
    return {'n': len(values), 'sum_seconds': sum(values), 'mean_seconds': statistics.mean(values),
            'median_seconds': statistics.median(values), 'p95_seconds': values[math.ceil(.95 * len(values))-1]}


def summarize_generation(records, crates, slots=20):
    terminal_statuses = {'generation_failed', 'generation_timeout', 'planning_unknown',
                         'infrastructure_failed'}

    def summarize(items, expected):
        done = [r for r in items if r.get('finished') or r.get('status') in terminal_statuses]
        responses = [r for r in items if r.get('attempts') and r['attempts'][0].get('response_received')]
        accepted = sum(r.get('accepted', False) for r in done)
        first = sum(bool(r['attempts'][0].get('accepted')) for r in responses)
        first_compiled = sum(bool(r['attempts'][0].get('normalized_coverage_compile_success'))
                             for r in responses)
        attempts = [a for r in items for a in r.get('attempts', [])]
        preflights = [r['core_preflight'] for r in items if r.get('core_preflight')]
        costs=[Decimal(a['peak_estimate_cny']) for a in attempts if 'peak_estimate_cny' in a]
        usage = [a.get('usage', {}) for r in items for a in r.get('attempts', [])
                 if a.get('usage')]
        return {
            'fixed_target_slots': expected, 'generation_jobs_scheduled': len(items),
            'llm_jobs_dispatched': sum(bool(r.get('attempts')) for r in items),
            'llm_jobs_finished': sum(bool(r.get('attempts')) for r in done),
            'generation_jobs_finished': len(done),
            'core_preflight_rejected_jobs': sum(p['status']=='rejected' for p in preflights),
            'core_preflight_routed_to_llm_repair_jobs': sum(r.get('preflight_action')=='llm_repair' for r in items),
            'replanning_jobs':sum(bool(r.get('plan_lineage')) for r in items),
            'paid_duplicate_token_responses':sum(bool(a.get('paid_response_same_ast')) for a in attempts),
            'repeated_failure_code_responses':sum(bool(a.get('same_failure_fingerprint')) for a in attempts),
            'input_influence_applicability':dict(Counter(r.get('input_applicability',{}).get('influence_applicability','unknown') for r in items)),
            'accepted_after_replanning_jobs':sum(r.get('accepted',False) and bool(r.get('active_plan')) for r in done),
            'changed_plan_model_attempts':sum(bool(r.get('attempts')) and len({
                a.get('plan',{}).get('sha256') for a in r['attempts'] if a.get('plan')})>1 for r in items),
            'core_preflight_infrastructure_failed_jobs': sum(p['status']=='infrastructure_failed' for p in preflights),
            'core_preflight_wall_time': distribution([p.get('elapsed_seconds') for p in preflights]),
            'max_model_calls_per_target_observed': sorted({r['max_model_calls'] for r in items if 'max_model_calls' in r}),
            'first_responses_received': len(responses), 'first_response_admitted': first,
            'first_response_admission_rate': first / len(responses) if responses else None,
            'first_response_compile_success': first_compiled,
            'first_response_compile_rate': first_compiled / len(responses) if responses else None,
            'eventual_admitted': accepted, 'eventual_admission_rate_of_finished_jobs': accepted / len(done) if done else None,
            'admitted_fraction_of_fixed_slots_so_far': accepted / expected if expected else None,
            'backend_error_jobs': sum(bool(r.get('backend_error')) for r in done),
            'generation_failed_jobs': sum(r.get('status') == 'generation_failed' for r in done),
            'generation_timeout_jobs': sum(r.get('status') == 'generation_timeout' for r in done),
            'planning_unknown_jobs': sum(r.get('status') == 'planning_unknown' for r in done),
            'infrastructure_failed_jobs': sum(r.get('status') == 'infrastructure_failed' for r in done),
            'empty_response_attempts': sum(bool(a.get('empty_response')) for a in attempts),
            'length_truncated_attempts': sum(a.get('finish_reason') == 'length' for a in attempts),
            'repair_attempts': sum(a.get('kind') == 'repair' for a in attempts),
            'total_model_calls': sum(len(r.get('attempts', [])) for r in items),
            'successful_repair_jobs': sum(r.get('accepted', False) and len(r.get('attempts', [])) > 1 for r in done),
            'unique_accepted_source_hashes': len({r.get('source_sha256') for r in done
                                                  if r.get('accepted')
                                                  if r.get('source_sha256')}),
            'duplicate_accepted_source_jobs': sum(bool(r.get('duplicate_of')) for r in done),
            'job_wall_time': distribution([r.get('elapsed_seconds') for r in done]),
            'queue_wait_time': distribution([r.get('queue_wait_seconds') for r in items]),
            'time_to_first_response': distribution([r.get('time_to_first_response_seconds') for r in responses]),
            'model_call_time': distribution([a.get('model_seconds') for a in attempts]),
            'compile_and_validation_time': distribution([r.get('compile_validation_seconds') for r in done]),
            'actual_compile_time': distribution([c.get('seconds') for r in items
                                                  for a in r.get('attempts', [])
                                                  for c in a.get('compiler_runs', [])]),
            'prompt_tokens': sum(u.get('prompt_tokens', 0) for u in usage),
            'completion_tokens': sum(u.get('completion_tokens', 0) for u in usage),
            'usage_reported_peak_estimate_cny':str(sum(costs,Decimal(0))) if costs else None,
            'cost_priced_attempts':len(costs),
            'cost_note':'Response-usage estimates only. Include outstanding/failed-call reservations from model-cost ledgers for financial totals.',
        }
    return {
        'schema': 'graft-generation-metrics-v1',
        'definitions': {
            'admitted': 'Original planned wrapper compiles and passes the three-input planned-call probe; normalized wrapper passes policy, compiler, input/profiling checks and AFL compilation. Fuzzing completion is separate.',
            'first_response_admission_rate': 'Admitted first responses / received first responses; backend failures are reported separately.',
            'eventual_admission_rate_of_finished_jobs': 'Admitted / finished scheduled generation jobs, including preflight rejection and backend errors. Per-target max_model_calls records the actual repair limit.',
            'dispatch': 'Scheduled jobs are distinct from jobs with a model attempt. A preflight rejection may block generation or be routed to bounded LLM parameter repair, depending on the frozen profile. Local money-policy rejection may still have an attempted request; actual HTTP dispatch is counted by the batch model_requests counter.',
            'admitted_fraction_of_fixed_slots_so_far': 'Accepted so far / fixed target slots. Pending and planning-unknown slots are not represented as failed LLM calls.',
            'timing': 'Wall seconds; call and job durations overlap across workers. Sum of durations is not experiment elapsed time. Compile/validation includes waiting for compile slots.',
            'no_repair_cost': 'The no-repair configuration shares the first GRAFT response and incurs no extra initial model call.'},
        'overall': summarize(records, len(crates)*slots),
        'by_crate': {c: summarize([r for r in records if r['crate'] == c], slots) for c in crates},
        'targets': records,
    }


def summarize_admissions(methods, crates, identities, admissions, generator_runs, slots=20):
    """The same fixed-slot admission rates for native and LLM generators."""
    rows=[]
    for method in methods:
        for crate in crates:
            states=Counter(v['status'] for (m,c,i),v in identities.items() if m==method and c==crate)
            targets=[v for (m,c,i),v in admissions.items() if m==method and c==crate]
            first=sum(bool(v and v[0]['accepted']) for v in targets)
            eventual=sum(any(a['accepted'] for a in v) for v in targets)
            rows.append({'method':method,'crate':crate,'fixed_target_slots':slots,
                'status_counts':dict(states),'target_slots_with_admission_attempts':len(targets),
                'first_attempt_admitted':first,'eventually_admitted':eventual,
                'first_attempt_admission_fraction_of_fixed_slots_so_far':first/slots,
                'eventual_admission_fraction_of_fixed_slots_so_far':eventual/slots,
                'admission_attempt_wall_time':distribution([a['elapsed_seconds'] for v in targets for a in v if 'elapsed_seconds' in a]),
                'native_generator_batch':generator_runs.get(method,{}).get(crate),
                'targets':[{'slot':i,'attempts':v} for (m,c,i),v in admissions.items() if m==method and c==crate]})
    return {'schema':'graft-all-method-generation-v1',
        'definitions':{'success':'Same final admission gate for every method, separate from successful fuzz execution.',
            'rates':'Fractions of the same fixed target-slot budget. During execution these are progress fractions, not final success rates.',
            'first_attempt':'For no-repair, this is the shared first GRAFT response. Native generators retain their own internal candidate search; that search is not equated to one LLM response.',
            'native_timing':'Native generation is timed per crate batch; it is not divided by target count or added once per target.',
            'timing':'Wall seconds. Compile-slot waiting and actual compiler time are separate in each attempt. Concurrent times must not be added to estimate experiment elapsed time.'},
        'rows':rows}
