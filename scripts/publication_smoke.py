#!/usr/bin/env python3
"""Build the real paper template with synthetic, clearly isolated publication rows."""
import argparse
import json
import shutil
from pathlib import Path

from run_experiment_rounds import file_hash, load, publish, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--paper-source', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    args = parser.parse_args()
    if args.root.exists():shutil.rmtree(args.root)
    args.root.mkdir(parents=True)
    paper=args.root/'paper'
    shutil.copytree(args.paper_source,paper)
    protocol=load(args.protocol)
    protocol['_file_sha256']=file_hash(args.protocol)
    rows=[];generation_rows=[]
    for method in protocol['methods']:
        for crate in protocol['versions']:
            rows.append({'method':method,'crate':crate,'round':1,'status':'complete',
                'slot_status_counts':{'measured':20},'denominator':10,
                'confirmed_covered_lower_bound':1,'coverage_percent':10.0,
                'measured_targets':20,'upstream_failure_slots':0,'infrastructure_gap_slots':0,
                'afl_executions':20,'raw_saved_crashes_not_bugs':0,'raw_saved_hangs':0,
                'covered_api_ids':['fixture']})
            generation_rows.append({'method':method,'crate':crate,'fixed_target_slots':20,
                'status_counts':{'ready':20},'target_slots_with_admission_attempts':20,
                'first_attempt_admitted':10,'eventually_admitted':20,
                'first_attempt_admission_fraction_of_fixed_slots_so_far':.5,
                'eventual_admission_fraction_of_fixed_slots_so_far':1.0,
                'admission_attempt_wall_time':{'n':20,'sum_seconds':20,
                    'mean_seconds':1,'median_seconds':1,'p95_seconds':2},
                'native_generator_batch':None,'targets':[]})
    summary={'round':1,'status':'complete','rows':rows,'schema':'graft-round-summary-v1',
             'note':'synthetic publication smoke; not scientific evidence'}
    generation={'schema':'graft-all-method-generation-v1','rows':generation_rows}
    save(args.root/'round-01/summary.json',summary)
    save(args.root/'round-01/all-method-generation.json',generation)
    publish(protocol,args.root,paper,1,summary,generation)
    report={'status':'passed','scope':'synthetic publication smoke; not formal data',
            'pdf':str(paper/'out/short-budget/round-01/main.pdf'),
            'include':str(paper/'chapters/generated/short-budget.tex')}
    (args.root/'publication-smoke.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
