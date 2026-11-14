#!/usr/bin/env python3
"""Compile actual saved baseline replays and inventory the new frozen catalogs.

This is preparation evidence, never a completed formal round. No generated replay
is executed here, and no static keyword screen is treated as an unsafe proof.
"""
import argparse
import concurrent.futures
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import time

from coverage_snapshot import file_hash, load
from run_experiment_rounds import save


def review_flags(text):
    flags = []
    for pattern, reason in [(r'\bunsafe\b', 'unsafe token requires contract review'),
                            (r'\bas\s+\*\s*(?:mut|const)\b', 'raw pointer conversion requires review'),
                            (r'\b(?:(?:std::)?fs::(?:write|remove|create|rename)|File::create|Command::|process::Command)',
                             'filesystem mutation/process creation requires review')]:
        if re.search(pattern, text):
            flags.append(reason)
    return flags


def run(command, log, timeout=60):
    start = time.monotonic()
    try:
        with log.open('wb') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        return {'command': list(map(str, command)), 'returncode': result.returncode,
                'seconds': time.monotonic() - start, 'log': str(log)}
    except subprocess.TimeoutExpired:
        return {'command': list(map(str, command)), 'returncode': None, 'timed_out': True,
                'seconds': time.monotonic() - start, 'log': str(log)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', type=Path, required=True)
    p.add_argument('--protocol', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--planner', type=Path, required=True)
    p.add_argument('--jobs', type=int, default=8)
    a = p.parse_args()
    port, out = a.port.resolve(), a.output.resolve()
    if out.exists():
        raise SystemExit('new output directory required')
    out.mkdir(parents=True)
    protocol = load(a.protocol)
    deps = port / 'modern-target-v2/debug/deps'
    rustc = subprocess.check_output(['rustup', 'which', '--toolchain', 'stable', 'rustc'], text=True).strip()
    compiles = []
    summaries = {tool: load(port / 'generator-preflight-v2' / tool / 'summary.json') for tool in ['rpg', 'rulf']}
    for tool in ['rpg', 'rulf']:
        for crate, version in protocol['versions'].items():
            base = port / 'generator-preflight-v2' / tool / crate if tool == 'rpg' else port / 'rulf-work-v2' / (crate.replace('-', '_') + '-work')
            replays = sorted(base.rglob('replay*.rs')) if base.exists() else []
            # This ordering is a reproducible compile audit, not a favorable
            # selection by success, coverage, or crashes.
            for index, source in enumerate(replays[:protocol['target_slots']]):
                directory = out / tool / crate / str(index)
                directory.mkdir(parents=True)
                libraries = list(deps.glob('lib' + crate.replace('-', '_') + '-*.rlib'))
                libraries = [lib for lib in libraries if (lib.parent / (lib.stem[3:] + '.d')).is_file()
                    and '/' + crate + '-' + version + '/' in (lib.parent / (lib.stem[3:] + '.d')).read_text()]
                if len(libraries) != 1:
                    raise ValueError(f'ambiguous exact-version rlib: {crate}')
                compiles.append((tool, crate, version, index, source, libraries[0], directory))

    def compile_one(spec):
        tool, crate, version, slot, source, library, directory = spec
        text = source.read_text()
        flags = review_flags(text)
        command = [rustc, '--edition=2021', str(source), '--extern', crate.replace('-', '_') + '=' + str(library),
                   '-L', 'dependency=' + str(deps), '-o', str(directory / 'replay')]
        compiled = run(command, directory / 'compile.log', 60)
        result = {'tool': tool, 'crate': crate, 'version': version, 'slot': slot,
            'source': str(source), 'source_sha256': file_hash(source), 'compilation': compiled,
            'static_review_flags': flags, 'execution': 'not_run',
            'formal_ready': False, 'remaining': ['AFL instrumentation', 'matched cfg/feature proof',
                'actual wrapper/input-arrival probe', 'unsafe and side-effect review', 'coverage replay adapter']}
        save(directory / 'preflight.json', result)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        rows = list(pool.map(compile_one, compiles))
    catalogs = []
    for crate, version in protocol['versions'].items():
        directory = out / 'catalogs' / crate
        directory.mkdir(parents=True)
        source = port / 'frozen-rustdoc-v2' / (crate + '-' + version + '.json')
        catalog = directory / 'catalog.json'
        imported = run([str(a.planner), 'import', str(source), str(catalog), '--package', crate], directory / 'import.log')
        row = {'crate': crate, 'version': version, 'import': imported}
        if imported['returncode'] == 0:
            row['inventory'] = run([str(a.planner), 'inventory', str(catalog), str(directory / 'inventory.json')], directory / 'inventory.log')
        catalogs.append(row)
    counts = dict(Counter((r['tool'] + ':' + ('compiled' if r['compilation']['returncode'] == 0 else 'compile_failed')) for r in rows))
    report = {'schema': 'graft-round-preparation-v1', 'status': 'preparation_only_not_formal_round',
        'protocol_sha256': file_hash(a.protocol), 'selected_saved_replays': len(rows), 'compile_counts': counts,
        'static_review_flagged': sum(bool(r['static_review_flags']) for r in rows),
        'formal_ready': 0, 'fuzz_runs': 0, 'catalogs': catalogs, 'targets': rows,
        'generator_preflight': {tool: [{'crate': r['crate'], 'version': r['version'],
            'generation': r['generation'], 'raw_targets': r['generated_target_count']} for r in data]
            for tool, data in summaries.items()},
        'limitations': ['Saved baseline generation was a 60-second compatibility preflight, not six independent generation rounds.',
            'Modern compiler replay compilation is a broader validity diagnostic; it is not original-compiler or instrumented-harness evidence.',
            'Keyword flags are conservative review queues, not a sound unsafe analyzer.',
            'Missing generation/AFL adapters cannot be replaced with empty or synthetic formal rounds.']}
    save(out / 'preparation.json', report)
    print(json.dumps({'selected': len(rows), 'compile_counts': counts, 'flagged_for_review': report['static_review_flagged'],
                      'catalog_imports': sum(c['import']['returncode'] == 0 for c in catalogs), 'formal_ready': 0}), flush=True)


if __name__ == '__main__':
    main()
