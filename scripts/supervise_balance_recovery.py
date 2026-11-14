#!/usr/bin/env python3
"""Wait for deepSURF repair processes, then resume the formal round safely."""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path


def alive(pid):
    try:
        state = Path(f'/proc/{pid}/stat').read_text().split()[2]
        return state != 'Z' and os.kill(pid, 0) is None
    except (OSError, IndexError, FileNotFoundError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--crates', required=True)
    parser.add_argument('--timeout', type=int, default=21600)
    args = parser.parse_args()
    crates = args.crates.split(',')
    started = time.monotonic()
    outcome = Path('/home/cym/workspace/graft-full-round1-20261110/balance-recovery-supervisor.json')
    while True:
        missing = [c for c in crates
                   if not (args.root/'deepsurf-native/round-01'/c/'outcomes.json').exists()]
        if not missing:
            break
        if not alive(args.pid):
            outcome.write_text(json.dumps({'status': 'failed', 'missing': missing,
                'reason': 'deepSURF repair process exited before all outcomes were written'},
                indent=2)+'\n')
            return 1
        if time.monotonic()-started > args.timeout:
            outcome.write_text(json.dumps({'status': 'failed', 'missing': missing,
                'reason': 'deepSURF repair supervisor timeout'}, indent=2)+'\n')
            return 1
        time.sleep(20)
    command = ['flock', '-n', str(args.root/'.run.lock'), 'python3', '-u',
               '/home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/launch_controlled_round.py',
               '--root', str(args.root), '--auto-second', '--resume']
    with (args.root/'balance-retry-launcher.log').open('wb') as log:
        process = subprocess.Popen(command, cwd='/home/cym/workspace/GRAFT-2.0/GRAFTTool',
            env={k:v for k,v in os.environ.items()
                 if not any(s in k.upper() for s in ('API_KEY','TOKEN','SECRET','AUTHORIZATION'))},
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)
    outcome.write_text(json.dumps({'status': 'launcher_started', 'pid': process.pid,
        'command': command, 'deepsurf_crates': crates}, indent=2)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
