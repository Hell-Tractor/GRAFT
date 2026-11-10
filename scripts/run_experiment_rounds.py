#!/usr/bin/env python3
"""Six round barrier runner. Only sealed, prepared targets may reach AFL.

Commands are argument arrays, never shell fragments. Replay must produce an LLVM
export; API entry counts are recomputed here against the frozen universe. A round
is published to the paper before any task of the next round may start.
"""
import argparse
import concurrent.futures
from collections import Counter
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import time

from coverage_snapshot import file_hash, load, measure, verify


UPSTREAM_FAILURES = {'generation_failed', 'generation_timeout', 'compile_failed',
                     'invalid_harness', 'no_target', 'planning_unknown', 'validation_rejected'}


def atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    tmp.replace(path)


def save(path, value):
    atomic(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def seed_for(root_seed, round_number, crate, slot):
    # Excluding the method gives paired seeds across methods. Generation and
    # fuzzing use separate domains; no claim that model sampling is seedable.
    key = f'{root_seed}:afl:{round_number}:{crate}:{slot}'.encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], 'big') or 1


def checked_file(record):
    path = Path(record['path'])
    if not path.is_absolute() or file_hash(path) != record['sha256']:
        raise ValueError(f'absent or changed sealed artifact: {path}')
    return path


def afl_statistics(directory, workers):
    paths = sorted(Path(directory).rglob('fuzzer_stats'))
    if len(paths) != workers:
        raise ValueError('missing or unexpected AFL worker statistics')
    rows = []
    for path in paths:
        values = dict(line.split(':', 1) for line in path.read_text().splitlines() if ':' in line)
        values = {k.strip(): v.strip() for k, v in values.items()}
        row = {k: int(values[k]) for k in ['run_time', 'execs_done', 'saved_crashes', 'saved_hangs']}
        row['path'] = str(path)
        if row['execs_done'] <= 0:
            raise ValueError('no actual AFL executions')
        rows.append(row)
    return rows


def check_tasks(protocol, manifest, round_number):
    """Missing targets stay visible; they cannot be silently dropped or zeroed."""
    expected = {(m, c, i) for m in protocol['methods']
                for c in protocol['versions'] for i in range(protocol['target_slots'])}
    seen = set()
    if manifest['round'] != round_number:
        raise ValueError('wrong round manifest')
    if manifest['protocol_sha256'] != protocol['_file_sha256']:
        raise ValueError('round protocol mismatch')
    for task in manifest['tasks']:
        key = (task['method'], task['crate'], task['slot'])
        if key in seen or key not in expected:
            raise ValueError(f'duplicate or unexpected task: {key}')
        seen.add(key)
        if task.get('status') == 'pending':
            raise ValueError(f'unprepared task: {key}')
        if task['status'] in UPSTREAM_FAILURES:
            checked_file(task['evidence'])
            continue
        if task['status'] != 'ready':
            raise ValueError(f'unknown preparation status: {key}')
        if task['version'] != protocol['versions'][task['crate']]:
            raise ValueError('crate version mismatch')
        for name in ('binary', 'source', 'validity_evidence', 'build_evidence', 'initial_corpus_manifest'):
            checked_file(task[name])
        corpus = load(task['initial_corpus_manifest']['path'])
        if not corpus.get('files'):
            raise ValueError('empty initial corpus')
        for item in corpus['files']:
            checked_file(item)
        for gate in ('unsafe_contracts_reviewed', 'wrapper_probe_passed',
                     'shared_lock_features_verified', 'instrumentation_cfg_verified'):
            if task.get(gate) is not True:
                raise ValueError(f'missing readiness evidence: {gate} {key}')
        if task.get('workers') != protocol['workers_per_target']:
            raise ValueError('worker budget differs across methods')
        for name in ('fuzz_command', 'replay_command'):
            command = task.get(name)
            if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
                raise ValueError('commands must be nonempty string arrays')
        for name, val in task.get('environment', {}).items():
            if not isinstance(name, str) or not isinstance(val, str):
                raise ValueError('environment entries must be strings')
    if seen != expected:
        raise ValueError(f'{len(expected - seen)} missing target slots')
    return manifest['tasks']


def command_run(command, cwd, env, logfile, timeout, cpus=None):
    start = time.monotonic()
    # taskset is part of the command rather than preexec_fn: this is safe in a
    # multithreaded launcher and constrains descendants too.
    if cpus:
        command = ['taskset', '--cpu-list', ','.join(map(str, cpus))] + command
    with Path(logfile).open('wb') as log:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        finally:
            # Retire any remaining forkserver or follower, including when the
            # leader exits early. Do not leave a previous round consuming CPUs.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return {'command': command, 'returncode': proc.returncode, 'timed_out': timed_out,
            'seconds': time.monotonic() - start, 'log': str(logfile)}


def execute(task, protocol, number, directory, cpus, universe):
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / 'result.json'
    task_hash = hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest()
    if result_path.exists():
        old = load(result_path)
        if old['task_sha256'] != task_hash:
            raise ValueError('resume task differs; use a new batch directory')
        return old
    result = {k: task[k] for k in ('method', 'crate', 'slot')}
    result.update(round=number, task_sha256=task_hash, status=task['status'])
    if task['status'] in UPSTREAM_FAILURES:
        result['evidence'] = task['evidence']
        save(result_path, result)
        return result
    # Never resume an interrupted corpus with a fresh nominal 300-second budget.
    if (directory / 'started.json').exists():
        result['status'] = 'interrupted'
        save(result_path, result)
        return result
    save(directory / 'started.json', {'unix_seconds': time.time(), 'task_sha256': task_hash})
    values = {'run_dir': str(directory), 'seconds': str(protocol['seconds_per_target']),
              'seed': str(seed_for(protocol['root_seed'], number, task['crate'], task['slot'])),
              'coverage_export': str(directory / 'coverage-export.json')}
    # Exact token replacement does not interpret Rust braces or shell syntax.
    def expand(command):
        for key, val in values.items():
            command = [arg.replace('{' + key + '}', val) for arg in command]
        return command
    env = {k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('API_KEY','TOKEN','SECRET','AUTHORIZATION'))}
    env.update(task.get('environment', {}))
    env['GRAFT_RUN_DIR'] = str(directory)
    result['seed'] = int(values['seed'])
    try:
        result['fuzz'] = command_run(expand(task['fuzz_command']), directory, env,
            directory / 'fuzz.log', protocol['seconds_per_target'] + 60, cpus)
        fuzz = result['fuzz']
        # AFL -V must stop itself. A successful exit before the budget is not a
        # completed experiment (e.g. an empty corpus or failed forkserver).
        if fuzz['returncode'] != 0 or fuzz['timed_out'] or fuzz['seconds'] < protocol['seconds_per_target'] - 2:
            result['status'] = 'fuzz_failed'
        else:
            result['afl_statistics'] = afl_statistics(directory, task['workers'])
            if any(s['run_time'] < protocol['seconds_per_target']-2 for s in result['afl_statistics']):
                raise ValueError('AFL statistics do not confirm the full per-target budget')
            result['replay'] = command_run(expand(task['replay_command']), directory, env,
                directory / 'replay.log', protocol['replay_timeout_seconds'], cpus)
            replay = result['replay']
            if replay['returncode'] != 0 or replay['timed_out']:
                result['status'] = 'replay_failed'
            else:
                measured = measure(universe, load(values['coverage_export']))
                measured['status'] = 'new_fuzzing_corpus_replay'
                measured['coverage_sha256'] = file_hash(values['coverage_export'])
                save(directory / 'coverage-audit.json', measured)
                result.update(status='measured', covered_api_ids=[a['api_id'] for a in measured['apis']
                    if a['public_name_body_definition'] and a['status'] == 'covered'],
                    coverage_sha256=measured['coverage_sha256'], universe_sha256=universe['universe_sha256'])
    except Exception as error:
        result.update(status='infrastructure_failed', error=str(error))
    save(result_path, result)
    return result


def summarize(protocol, results, universes, round_number):
    rows = []
    for method in protocol['methods']:
        for crate in protocol['versions']:
            items = [r for r in results if r['method'] == method and r['crate'] == crate]
            allowed = {a['api_id'] for a in universes[crate]['apis'] if a.get('public_name_body_definition')}
            covered = set()
            for result in items:
                ids = set(result.get('covered_api_ids', []))
                if not ids <= allowed:
                    raise ValueError('coverage contains an API outside the frozen denominator')
                covered.update(ids)
            counts = Counter(r['status'] for r in items)
            complete = len(items) == protocol['target_slots'] and all(
                r['status'] == 'measured' or r['status'] in UPSTREAM_FAILURES for r in items)
            rows.append({'method': method, 'crate': crate, 'round': round_number,
                'status': 'complete' if complete else 'incomplete', 'slot_status_counts': dict(counts),
                'denominator': len(allowed), 'confirmed_covered_lower_bound': len(covered),
                'coverage_percent': 100 * len(covered) / len(allowed) if complete and allowed else None,
                'measured_targets': counts['measured'],
                'upstream_failure_slots': sum(counts[s] for s in UPSTREAM_FAILURES),
                'infrastructure_gap_slots': len(items)-counts['measured']-sum(counts[s] for s in UPSTREAM_FAILURES),
                'afl_executions': sum(s['execs_done'] for item in items for s in item.get('afl_statistics', [])),
                'raw_saved_crashes_not_bugs': sum(s['saved_crashes'] for item in items for s in item.get('afl_statistics', [])),
                'raw_saved_hangs': sum(s['saved_hangs'] for item in items for s in item.get('afl_statistics', [])),
                'covered_api_ids': sorted(covered)})
    return {'schema': 'graft-round-summary-v1', 'round': round_number,
            'status': 'complete' if all(r['status'] == 'complete' for r in rows) else 'incomplete',
            'seconds_per_target': protocol['seconds_per_target'], 'rows': rows,
            'note': 'Union target coverage within crate/round only; failure slots retained; infrastructure gaps are not zero coverage.'}


def cumulative(protocol, reports):
    result = []
    for method in protocol['methods']:
        values = []
        for report in reports:
            rows = [r for r in report['rows'] if r['method'] == method]
            if len(rows) == len(protocol['versions']) and all(r['coverage_percent'] is not None for r in rows):
                values.append(statistics.mean(r['coverage_percent'] for r in rows))
        result.append({'method': method, 'complete_rounds': len(values), 'attempted_rounds': len(reports),
            'macro_mean_percent': statistics.mean(values) if values else None,
            'macro_sample_sd': statistics.stdev(values) if len(values) > 1 else None})
    return result


def latex_escape(text):
    return ''.join({'&': r'\&', '%': r'\%', '_': r'\_', '#': r'\#',
                    '{': r'\{', '}': r'\}', '\\': r'\textbackslash{}'}.get(c, c) for c in text)


def publish(protocol, batch, paper, number, summary, generation=None):
    destination = paper / 'revisions' / 'short-budget' / f'round-{number:02d}'
    destination.mkdir(parents=True, exist_ok=True)
    reports = [load(batch / f'round-{i:02d}' / 'summary.json') for i in range(1, number + 1)]
    stats = cumulative(protocol, reports)
    if generation is None:
        generation_path = batch/f'round-{number:02d}'/'all-method-generation.json'
        generation = load(generation_path) if generation_path.exists() else None
    if generation is not None:
        expected = len(protocol['methods'])*len(protocol['versions'])
        if len(generation.get('rows',[])) != expected:
            raise ValueError('generation statistics do not cover every method/crate row')
    generation_by_round={}
    for report_number in range(1,number+1):
        path=batch/f'round-{report_number:02d}'/'all-method-generation.json'
        if path.exists():generation_by_round[str(report_number)]=load(path)
    save(destination / 'statistics.json', {'through_round': number, 'methods': stats,
        'target_status_counts': dict(sum((Counter(r['slot_status_counts']) for report in reports for r in report['rows']), Counter())),
        'independent_generation_rounds': number, 'independent_afl_seeds_per_generation': 1,
        'planned_rounds': protocol['rounds'], 'per_target_seconds': protocol['seconds_per_target'],
        'generation': generation, 'generation_by_round': generation_by_round})
    save(destination / 'summary.json', summary)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=['round', 'method', 'crate', 'status', 'denominator',
        'confirmed_covered_lower_bound', 'coverage_percent'])
    writer.writeheader()
    writer.writerows({k: r[k] for k in writer.fieldnames} for report in reports for r in report['rows'])
    atomic(destination / 'coverage.csv', buf.getvalue())
    if generation is not None:
        buf = io.StringIO()
        generation_fields = ['method','crate','fixed_target_slots',
            'target_slots_with_admission_attempts','first_attempt_admitted','eventually_admitted',
            'status_counts','admission_attempt_wall_time','native_generator_batch']
        writer = csv.DictWriter(buf, fieldnames=generation_fields)
        writer.writeheader()
        writer.writerows({k: json.dumps(r.get(k),sort_keys=True) if isinstance(r.get(k),(dict,list))
                          else r.get(k) for k in generation_fields} for r in generation['rows'])
        atomic(destination / 'generation.csv', buf.getvalue())
    tex = [r'\paragraph{Short-budget rerun progress.}',
        f'After round {number} of {protocol["rounds"]}, each eligible target receives '
        f'{protocol["seconds_per_target"]} seconds per round. The table reports mean macro API entry coverage and sample standard deviation '
        'over fully measured rounds. Each round uses an independent generation batch and AFL seed; '
        'the no-repair arm shares the initial Full response. Missing measurements remain unavailable. '
        'These short runs do not replace longer-budget evidence.',
        r'\begin{center}\small\begin{tabular}{lrrr}', r'Configuration & Complete rounds & Mean (\%) & SD \\ \hline']
    for row in stats:
        mean = '--' if row['macro_mean_percent'] is None else f'{row["macro_mean_percent"]:.2f}'
        sd = '--' if row['macro_sample_sd'] is None else f'{row["macro_sample_sd"]:.2f}'
        tex.append(f'{latex_escape(row["method"])} & {row["complete_rounds"]} & {mean} & {sd} ' + r'\\')
    tex += [r'\end{tabular}\end{center}', '']
    if generation is not None:
        method_rows={}
        for row in generation['rows']:
            aggregate=method_rows.setdefault(row['method'],{'slots':0,'attempted':0,'first':0,
                'eventual':0,'admission_n':0,'admission_sum':0.0})
            aggregate['slots']+=row['fixed_target_slots']
            aggregate['attempted']+=row['target_slots_with_admission_attempts']
            aggregate['first']+=row['first_attempt_admitted']
            aggregate['eventual']+=row['eventually_admitted']
            timing=row.get('admission_attempt_wall_time',{})
            aggregate['admission_n']+=timing.get('n',0)
            aggregate['admission_sum']+=timing.get('sum_seconds',0)
        tex += [r'\paragraph{Generation and admission statistics.}',
            'Fixed target slots are reported separately from model responses, compilation, admission, and actual fuzzing. '
            'The no-repair arm reuses the first GRAFT response. Native batch times are retained at crate granularity and are not multiplied by target slots.',
            r'\begin{center}\small\begin{tabular}{lrrrrr}', r'Configuration & Slots & Attempted & First admitted & Eventual & Admission mean (s) \\ \hline']
        for method in protocol['methods']:
            row=method_rows.get(method)
            if row is None:
                tex.append(f'{latex_escape(method)} & -- & -- & -- & -- & -- ' + r'\\')
                continue
            mean='--' if row['admission_n']==0 else f'{row["admission_sum"]/row["admission_n"]:.2f}'
            tex.append(f'{latex_escape(method)} & {row["slots"]} & {row["attempted"]} & '
                       f'{row["first"]} & {row["eventual"]} & {mean} ' + r'\\')
        tex += [r'\end{tabular}\end{center}',
            'The appended generation metrics contain the full status, repair, token, and timing distributions.']
    text = '\n'.join(tex)
    atomic(destination / 'results.tex', text)
    include = paper / 'chapters' / 'generated' / 'short-budget.tex'
    old = include.read_text() if include.exists() else None
    atomic(include, text)
    out = paper / 'out' / 'short-budget' / f'round-{number:02d}'
    out.mkdir(parents=True, exist_ok=True)
    command = [protocol['tectonic'], '--only-cached', '--keep-logs', '--keep-intermediates',
               '--outdir', str(out), 'main.tex']
    env = dict(os.environ, TECTONIC_CACHE_DIR=protocol['tectonic_cache'])
    try:
        built = command_run(command, paper, env, destination / 'paper-build.log', 180)
        if built['returncode'] != 0 or built['timed_out'] or not (out / 'main.pdf').is_file():
            raise RuntimeError('paper build failed; next round paused; see paper-build.log')
    except BaseException:
        if old is None:
            include.unlink(missing_ok=True)
        else:
            atomic(include, old)
        raise
    save(destination / 'publication.json', {'round': number, 'pdf': str(out / 'main.pdf'),
        'pdf_sha256': file_hash(out / 'main.pdf'), 'build': built,
        'protocol_sha256': protocol['_file_sha256']})
    save(paper / 'revisions' / 'short-budget' / 'latest.json', {'round': number,
        'statistics': str(destination / 'statistics.json'), 'pdf': str(out / 'main.pdf')})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol', type=Path, required=True)
    p.add_argument('--batch', type=Path, required=True)
    p.add_argument('--paper', type=Path, required=True)
    p.add_argument('--check-only', action='store_true')
    a = p.parse_args()
    protocol = load(a.protocol)
    protocol['_file_sha256'] = file_hash(a.protocol)
    for name in ('common_versions', 'shared_lock'):
        checked_file(protocol[name])
    batch, paper = a.batch.resolve(), a.paper.resolve()
    batch.mkdir(parents=True, exist_ok=True)
    with (batch / 'runner.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Pin the batch identity: completed rounds are never mixed with a changed protocol.
        identity = batch / 'protocol-identity.json'
        if identity.exists() and load(identity)['sha256'] != protocol['_file_sha256']:
            raise ValueError('batch protocol changed; use a new directory')
        save(identity, {'sha256': protocol['_file_sha256'], 'protocol': str(a.protocol.resolve())})
        universes = {c: load(checked_file(protocol['universes'][c])) for c in protocol['versions']}
        for c, u in universes.items():
            verify(u)
            if u['crate'] != c or u['version'] != protocol['versions'][c]:
                raise ValueError('universe version mismatch')
        available = sorted(os.sched_getaffinity(0))
        if protocol['max_workers'] > len(available):
            raise ValueError('requested CPUs exceed current affinity')
        cpus = available[:protocol['max_workers']]
        width = protocol['workers_per_target']
        bundles = [cpus[i:i+width] for i in range(0, len(cpus)-width+1, width)]
        if not bundles:
            raise ValueError('no worker bundle fits')
        for number in range(1, protocol['rounds'] + 1):
            rd = batch / f'round-{number:02d}'
            tasks = check_tasks(protocol, load(rd / 'tasks.json'), number)
            if a.check_only:
                continue
            # Freeze each prepared manifest before running it.
            mh = file_hash(rd / 'tasks.json')
            seal = rd / 'manifest-identity.json'
            if seal.exists() and load(seal)['sha256'] != mh:
                raise ValueError('prepared round manifest changed')
            save(seal, {'sha256': mh})
            # Mix methods/crates deterministically rather than running every
            # target of one method first under one particular host condition.
            tasks = sorted(tasks, key=lambda task: hashlib.sha256(
                f'{protocol["root_seed"]}:schedule:{number}:{task["method"]}:{task["crate"]}:{task["slot"]}'.encode()).digest())
            save(rd / 'execution-order.json', {'manifest_sha256': mh,
                'tasks': [{k: t[k] for k in ['method', 'crate', 'slot']} for t in tasks],
                'cpu_bundles': bundles})
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(bundles)) as pool:
                pending, queue = {}, iter(tasks)
                free = list(bundles)
                while True:
                    while free:
                        task = next(queue, None)
                        if task is None:
                            break
                        bundle = free.pop()
                        dest = rd / task['method'] / task['crate'] / str(task['slot'])
                        future = pool.submit(execute, task, protocol, number, dest, bundle, universes[task['crate']])
                        pending[future] = bundle
                    if not pending:
                        break
                    done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                    for future in done:
                        free.append(pending.pop(future))
                        results.append(future.result())
            summary = summarize(protocol, results, universes, number)
            save(rd / 'summary.json', summary)
            publish(protocol, batch, paper, number, summary)
            save(batch / 'progress.json', {'published_round': number, 'round_status': summary['status'],
                'status': 'finished' if number == protocol['rounds'] else 'running',
                'all_rounds_measured': all(load(batch / f'round-{i:02d}' / 'summary.json')['status'] == 'complete'
                    for i in range(1, number+1))})
            print(f'round {number}: {summary["status"]}; paper published', flush=True)


if __name__ == '__main__':
    main()
