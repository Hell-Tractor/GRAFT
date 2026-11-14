#!/usr/bin/env python3
"""Small non-formal Engine smoke test: native + GRAFT admission, fuzz and replay."""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from launch_controlled_round import Engine, PLANNER, ROOT
from run_experiment_rounds import load


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--native-source', type=Path, required=True)
    parser.add_argument('--crate', default='ryu')
    parser.add_argument('--skip-graft', action='store_true')
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    runtime = args.root/'runtime'
    if not runtime.exists():
        runtime.symlink_to('/home/cym/workspace/graft-full-round1-20261110/runtime', target_is_directory=True)
    engine = Engine(args.root, 1)
    engine.protocol['seconds_per_target'] = 5
    planned_api = None
    if not args.skip_graft:
        catalog, cat, full, random_order, context = engine.plans(args.crate)
        api, plan = full[0]
        planned_api = api['id']
        d = engine.directory('GRAFT', args.crate, 0)
        core = d/'core-prompt.txt'
        subprocess.run([str(PLANNER), 'prompt', str(plan), str(core)], check=True, capture_output=True)
        version = engine.protocol['versions'][args.crate]
        prompt = (f'Use only the pinned {args.crate} {version} default-feature library. '
                  f'Public documentation pool:\n{context}\n'+core.read_text())
        engine.generation[('GRAFT',args.crate,0)] = {'round':1,'method':'GRAFT',
            'crate':args.crate,'slot':0,'queued_unix_seconds':time.time(),
            'attempts':[],'finished':False,'accepted':False}
    native_ok = engine.admit('RPG', args.crate, 0, args.native_source, baseline=True)
    if not args.skip_graft:
        engine.model('GRAFT', args.crate, 0, prompt, plan, False)
    for future in engine.fuzz_futures:
        future.result(timeout=180)
    engine.fuzz_pool.shutdown()
    rows = []
    methods = ('RPG',) if args.skip_graft else ('RPG','GRAFT')
    for method in methods:
        key=(method,args.crate,0)
        rows.append({'method':method,'admitted':native_ok if method=='RPG' else
                     engine.generation.get(key,{}).get('accepted',False),
                     'task_status':engine.tasks.get(key,{}).get('status'),
                     'result_status':engine.results.get(key,{}).get('status'),
                     'result':engine.results.get(key,{})})
    report={'scope':'5-second non-formal smoke; excluded from formal rounds','planned_api':planned_api,
            'rows':rows}
    (args.root/'smoke.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    print(json.dumps(report,indent=2,default=str))
    if not all(row['result_status']=='measured' for row in rows):
        sys.exit(1)


if __name__ == '__main__':
    main()
