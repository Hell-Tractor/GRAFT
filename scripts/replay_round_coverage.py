#!/usr/bin/env python3
"""Replay every unique initial/queue input, then export LLVM entry coverage.

The replay binary must accept one input filename. Its build must have instrumented
the library, not just the wrapper. llvm-profdata/llvm-cov must match that compiler.
Nonzero exits/timeouts fail measurement; no failed replay is silently omitted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from coverage_snapshot import file_hash
from run_experiment_rounds import command_run, save


def corpus_files(initial, afl):
    files = sorted(p for p in Path(initial).rglob('*') if p.is_file())
    queues = sorted(p for p in Path(afl).rglob('queue') if p.is_dir())
    if not queues:
        raise ValueError('AFL output has no queue; no completed fuzzing evidence')
    files += sorted(p for queue in queues for p in queue.iterdir()
                    if p.is_file() and p.name.startswith('id:'))
    unique = {}
    for path in files:
        digest = file_hash(path)
        unique.setdefault(digest, []).append(str(path.resolve()))
    if not unique:
        raise ValueError('empty replay corpus')
    return unique


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--initial', type=Path, required=True)
    p.add_argument('--afl-output', type=Path, required=True)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--llvm-bin', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--input-timeout', type=float, default=3)
    a = p.parse_args()
    a.work = a.work.resolve()
    if a.work.exists():
        raise SystemExit('new coverage work directory required')
    a.work.mkdir(parents=True)
    binary = a.binary.resolve()
    corpus = corpus_files(a.initial, a.afl_output)
    report = {'schema': 'graft-corpus-replay-v1', 'binary': str(binary),
        'binary_sha256': file_hash(binary), 'inputs': [], 'status': 'running',
        'llvm_tool_sha256': {name: file_hash(a.llvm_bin/name) for name in ['llvm-profdata', 'llvm-cov']}}
    profiles = []
    for index, (digest, paths) in enumerate(sorted(corpus.items())):
        directory = a.work/str(index)
        directory.mkdir()
        # Copy the exact bytes: do not replay a queue path that AFL could change.
        data = Path(paths[0]).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('corpus changed during replay')
        sample = directory/'input'
        sample.write_bytes(data)
        env = dict(os.environ, LLVM_PROFILE_FILE=str(directory/'coverage-%p-%m.profraw'))
        run = command_run([str(binary), str(sample)], directory, env,
                          directory/'replay.log', a.input_timeout)
        raw = sorted(directory.glob('*.profraw'))
        report['inputs'].append({'sha256': digest, 'provenance_paths': paths, 'run': run,
            'profiles': [str(f) for f in raw]})
        save(a.work/'replay.json', report)
        if run['returncode'] != 0 or run['timed_out'] or not raw:
            report['status'] = 'failed_input_replay'
            save(a.work/'replay.json', report)
            raise SystemExit('a corpus input did not produce a successful instrumented replay')
        profiles += raw
    listed = a.work/'profiles.txt'
    listed.write_text(''.join(str(f)+'\n' for f in profiles))
    merged = a.work/'coverage.profdata'
    command = [str(a.llvm_bin/'llvm-profdata'), 'merge', '-sparse',
               '--input-files='+str(listed), '-o', str(merged)]
    report['merge'] = command_run(command, a.work, os.environ.copy(), a.work/'merge.log', 120)
    if report['merge']['returncode'] != 0 or report['merge']['timed_out']:
        report['status'] = 'profile_merge_failed'
        save(a.work/'replay.json', report)
        raise SystemExit('profile merge failed; check compiler/LLVM compatibility')
    a.output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(a.llvm_bin/'llvm-cov'), 'export', str(binary), '-instr-profile='+str(merged)]
    with a.output.open('wb') as output, (a.work/'export.log').open('wb') as error:
        result = subprocess.run(command, stdout=output, stderr=error, timeout=120)
    report['export'] = {'command': command, 'returncode': result.returncode}
    report['status'] = 'exported' if result.returncode == 0 else 'export_failed'
    if result.returncode == 0:
        report['export_sha256'] = file_hash(a.output)
    save(a.work/'replay.json', report)
    if result.returncode:
        raise SystemExit('LLVM coverage export failed')
    print(json.dumps({'unique_inputs': len(corpus), 'profiles': len(profiles), 'status': 'exported'}))


if __name__ == '__main__':
    main()
