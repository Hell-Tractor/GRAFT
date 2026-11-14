#!/usr/bin/env python3
"""Budget complete comparison matrices before starting costly jobs.

Harness runs and worker processes are separate: a master+follower consumes two slots.
CPU slots are logical process slots, not a claim of SMT throughput equivalence.
"""
import argparse
import json
import math
from pathlib import Path

def waves(groups,capacity):
    """Construct a feasible equal-duration schedule; pack two-worker bundles first."""
    if capacity<1 or any(g['workers']>capacity for g in groups):raise ValueError('insufficient worker capacity')
    remaining={}
    for g in groups:remaining[g['workers']]=remaining.get(g['workers'],0)+g['runs']
    count=0
    while any(remaining.values()):
        free=capacity
        for weight in sorted(remaining,reverse=True):
            n=min(remaining[weight],free//weight);remaining[weight]-=n;free-=n*weight
        count+=1
    return count

def estimate(scenario,slots,hours,overhead_hours,margin_hours,seconds=1800,rounds=1):
    groups=scenario['groups'];n=sum(g['runs'] for g in groups)
    worker_runs=sum(g['runs']*g['workers'] for g in groups)
    if seconds <= 0 or rounds < 1 or any(g['runs'] % rounds for g in groups):
        raise ValueError('positive duration and equally sized integral rounds required')
    # Publishing the paper creates a barrier between rounds: unused capacity in
    # a round cannot be filled with jobs from the next round.
    batches=rounds*waves([dict(g,runs=g['runs']//rounds) for g in groups],slots)
    usable=max(0,hours-overhead_hours-margin_hours)
    return {'scenario':scenario['name'],'harness_runs':n,'worker_runs':worker_runs,'slots':slots,'equal_duration_waves':batches,
        '30_min_fuzz_lower_bound_hours':worker_runs*.5/slots,'30_min_feasible_fuzz_hours':batches*.5,
        '60_min_feasible_fuzz_hours':batches,
        '30_min_with_reserves_hours':batches*.5+overhead_hours+margin_hours,
        'max_seconds_per_harness_within_deadline':math.floor(usable*3600/batches),
        'overhead_reserve_hours':overhead_hours,'margin_hours':margin_hours,
        'overhead_is_measured':False,'scope':scenario['scope'],
        'seconds_per_target':seconds,'rounds':rounds,
        'selected_fuzz_lower_bound_hours':worker_runs*seconds/slots/3600,
        'selected_feasible_fuzz_hours':batches*seconds/3600,
        'selected_with_reserves_hours':batches*seconds/3600+overhead_hours+margin_hours}

def scenarios(crates=13,slots_per_crate=20,repeats=10):
    def make(name,methods,slots,reps,scope):
        runs=crates*slots*reps
        return {'name':name,'scope':scope,'groups':[{'method':method,'runs':runs,'workers':workers} for method,workers in methods]}
    main=[('GRAFT',1),('RULF',1),('RPG',1),('deepSURF',2)]
    # Eight generation variants in package D1, with Full shared with the main comparison.
    extended=main+[(x,1) for x in ['LLM-only','random-API+LLM','syntactic+LLM','no-cross-argument-unification','random-target','no-repair','deterministic-emitter']]
    return [make('main_formal',main,slots_per_crate,repeats,f'{crates} crates, 4 tools, {slots_per_crate} target slots, {repeats} repetitions; deepSURF master+follower charged together'),
        make('all_variants_formal',extended,slots_per_crate,repeats,'All 8 generation variants plus 3 external baselines; Full shared; generation-batch and AFL-seed dimensions need further explicit decomposition'),
        make('main_exploratory_10_targets_3_repeats',main,10,3,'Reduced exploratory scope only; cannot replace the preregistered 10-20-repeat formal comparison')]

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--slots',type=int,nargs='+',default=[28,56,64,120]);p.add_argument('--hours',type=float,default=24);p.add_argument('--overhead-hours',type=float,default=6);p.add_argument('--margin-hours',type=float,default=2)
    p.add_argument('--crates',type=int,default=13);p.add_argument('--targets',type=int,default=20);p.add_argument('--repeats',type=int,default=10);p.add_argument('--seconds',type=int,default=1800);p.add_argument('--round-barriers',action='store_true');a=p.parse_args()
    if a.hours<=0 or min(a.slots)<1 or min(a.overhead_hours,a.margin_hours)<0:raise SystemExit('invalid resource budget')
    if min(a.crates,a.targets,a.repeats,a.seconds)<1:raise SystemExit('positive experiment dimensions required')
    cases=scenarios(a.crates,a.targets,a.repeats)
    def cost(s,c,index):
        rounds=(a.repeats if index<2 else 3) if a.round_barriers else 1
        return estimate(s,c,a.hours,a.overhead_hours,a.margin_hours,a.seconds,rounds)
    rows=[cost(s,c,i) for i,s in enumerate(cases) for c in a.slots]
    normalized=[]
    for i,s in enumerate(cases[:2]):
        for workers in [1,2]:
            matched={'name':s['name']+'_equal_'+str(workers)+'_workers','scope':'Equal worker count per harness is a scheduling sensitivity only; instrumented roles still need validation.','groups':[dict(g,workers=workers)for g in s['groups']]}
            normalized.extend(cost(matched,c,i)for c in a.slots)
    report={'schema':'graft-walltime-budget-v1','deadline_hours':a.hours,'observed_logical_cpus':64,'observed_physical_cores':32,'observed_recommended_logical_slots':56,'logical_120_scenario_is_counterfactual':True,'generation_build_replay_overhead_note':'Six-hour placeholder reserve must be calibrated before a run; model latency, corpus sizes, builds and generation counts are not yet measured. No unconditional 24-hour completion guarantee.','resource_fairness_note':'Natural topology charges deepSURF two workers and other tools one: it estimates cost but is not a resource-matched controlled comparison. Final manifests must equalize total worker budgets or validate equal worker roles.','groups':cases,'estimates':rows,'equal_workers_sensitivity':normalized}
    unit=a.crates*a.targets*a.repeats
    # Six LLM GRAFT variants + deepSURF. no-repair reuses Full's first
    # response, and the deterministic emitter makes no model requests.
    initial=7*unit;cap=28*unit
    report['generation_sensitivity']={'assumptions':f'{a.repeats} independent joint generation/AFL rounds, 16 concurrent requests; up to 3 repairs after initial except no-repair, which shares Full first response. deepSURF cap must be matched explicitly. Latencies unmeasured.',
        'main_model_calls_initial':2*unit,'main_model_calls_cap':8*unit,
        'all_variants_model_calls_initial':initial,'all_variants_model_calls_cap':cap,
        'all_variants_hours_at_16_requests':[{'seconds_per_request':s,'initial_only_hours':initial*s/16/3600,'at_call_cap_hours':cap*s/16/3600}for s in [5,20,60]],
        'compilation_sensitivity':{'parallel_compilers':8,'distinct_main_targets_upper_bound':4*unit,'distinct_all_variants_targets_upper_bound':11*unit,
            'hours_at_1_10_60_seconds_per_target':{'main':[4*unit*s/8/3600 for s in [1,10,60]],'all':[11*unit*s/8/3600 for s in [1,10,60]]},
            'note':'Additional repair builds and replay are excluded; cache reuse can reduce costs.'}}
    report['requested']={'crates':a.crates,'targets':a.targets,'repeats':a.repeats,'seconds':a.seconds,'round_barriers':a.round_barriers}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n')
    for r in rows:
        if r['slots']==56:print(r['scenario'],'selected fuzz hours=',r['selected_feasible_fuzz_hours'],'total incl unmeasured reserves=',r['selected_with_reserves_hours'],'hours')
if __name__=='__main__':main()
