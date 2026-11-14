#!/usr/bin/env python3
"""Run one proven base64 target for the requested 300 seconds, then replay.

This is a first-round execution probe, not completion of the blocked 11-method
matrix. No credentials or model requests are involved. All artifacts are retained.
"""
import argparse
import csv
import datetime
import io
import json
import os
from pathlib import Path
import sys
import time

from coverage_snapshot import file_hash, load, measure
from run_experiment_rounds import afl_statistics, command_run, save, seed_for

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, default=ROOT.parent.parent/'graft-experiments-5min-6rounds-20261109')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit('new output directory required')
    base = args.batch.resolve()/'executor-smoke'
    binary, replay = base/'afl-build/debug/fuzz', base/'coverage-build/debug/replay'
    afl = Path('/home/cym/.local/share/afl.rs/rustc-1.97.0-nightly-17584a1/afl.rs-0.18.2/afl/bin/afl-fuzz')
    llvm = Path('/home/cym/.rustup/toolchains/nightly-2026-02-07-x86_64-unknown-linux-gnu/lib/rustlib/x86_64-unknown-linux-gnu/bin')
    universe_path = ROOT/'experiments/week4/common-universes-v2/api_universes/base64.json'
    for path in [binary, replay, afl, llvm/'llvm-profdata', llvm/'llvm-cov', universe_path]:
        if not path.is_file():
            raise SystemExit('missing artifact: '+str(path))
    output.mkdir(parents=True)
    initial = output/'initial'
    initial.mkdir()
    inputs = []
    for source in sorted((base/'initial').iterdir()):
        if source.is_file():
            dest = initial/source.name
            dest.write_bytes(source.read_bytes())
            inputs.append({'path': str(dest), 'sha256': file_hash(dest), 'bytes': dest.stat().st_size})
    if not inputs:
        raise ValueError('empty initial corpus')
    manifest = {'scope': 'single-target first-round execution probe; not the full comparison',
        'full_matrix_complete': False, 'formal_rounds_completed': 0,
        'method': 'deterministic-emitter', 'crate': 'base64', 'version': '0.22.1',
        'api': 'base64::encoded_len', 'seconds_per_target': 300, 'worker_count': 1,
        'seed': seed_for(20261109, 1, 'base64', 0),
        'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'launcher_pid': os.getpid(), 'status': 'running', 'initial_inputs': inputs,
        'artifact_sha256': {str(p): file_hash(p) for p in [binary, replay, afl,
            base/'project/src/bin/fuzz.rs', base/'project/src/bin/replay.rs', base/'project/Cargo.lock', universe_path]},
        'limitations': ['11-configuration generation adapters remain incomplete; no method ranking is inferred.',
            'Diagnostic uses saved dev builds: AFL compiler nightly 1.97, replay compiler nightly 1.95; this is not the final matched instrumentation configuration.',
            'No ASan build or valid library bug claim from this probe.']}
    save(output/'result.json', manifest)
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ['API_KEY', 'TOKEN', 'SECRET', 'AUTHORIZATION'])}
    env.update(AFL_NO_UI='1', AFL_NO_AFFINITY='1', AFL_SKIP_CPUFREQ='1',
               AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES='1')
    command = [str(afl), '-V', '300', '-s', str(manifest['seed']), '-i', str(initial),
               '-o', str(output/'afl'), '-m', 'none', '-t', '1000', '--', str(binary)]
    start = time.monotonic()
    try:
        cpu = min(os.sched_getaffinity(0))
        manifest['fuzz'] = command_run(command, output, env, output/'fuzz.log', 360, [cpu])
        manifest['afl_statistics'] = afl_statistics(output/'afl', 1)
        save(output/'result.json', manifest)
        if manifest['fuzz']['returncode'] != 0 or manifest['fuzz']['timed_out'] or manifest['afl_statistics'][0]['run_time'] < 298:
            raise RuntimeError('AFL did not complete the requested budget')
        command = [sys.executable, str(ROOT/'scripts/replay_round_coverage.py'),
            '--initial', str(initial), '--afl-output', str(output/'afl'), '--binary', str(replay),
            '--llvm-bin', str(llvm), '--output', str(output/'coverage-export.json'), '--work', str(output/'replayed')]
        manifest['replay'] = command_run(command, output, env, output/'replay.log', 1800, [cpu])
        if manifest['replay']['returncode'] != 0 or manifest['replay']['timed_out']:
            raise RuntimeError('corpus replay failed')
        coverage = measure(load(universe_path), load(output/'coverage-export.json'))
        coverage['status'] = '300_second_execution_probe_only_not_comparative_result'
        coverage['coverage_sha256'] = file_hash(output/'coverage-export.json')
        save(output/'coverage-audit.json', coverage)
        planned = [a for a in coverage['apis'] if a['name'] == 'encoded_len' and a['public_name_body_definition']]
        if len(planned) != 1 or planned[0]['status'] != 'covered':
            raise RuntimeError('planned API entry not confirmed')
        manifest['coverage'] = coverage['groups']['public_name_body_definitions']
        manifest['unique_replayed_inputs'] = len(load(output/'replayed/replay.json')['inputs'])
        manifest['status'] = 'completed_execution_probe'
    except Exception as error:
        manifest.update(status='failed_execution_probe', error=str(error))
    manifest['elapsed_seconds'] = time.monotonic()-start
    manifest['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save(output/'result.json', manifest)
    if manifest['status'] == 'completed_execution_probe':
        row = {'crate': 'base64', 'version': '0.22.1', 'seconds': 300,
            **manifest['afl_statistics'][0], **manifest['coverage']}
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(row))
        writer.writeheader(); writer.writerow(row)
        (output/'summary.csv').write_text(buffer.getvalue())
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    return 0 if manifest['status'] == 'completed_execution_probe' else 1


if __name__ == '__main__':
    sys.exit(main())
