#!/usr/bin/env python3
"""Offline paired core preflight on an audited batch; no model or fuzz dispatch."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import time

from admission_diagnostics import atomic_json, fingerprint
from launch_controlled_round import compiler_template, clean_env, has_required_compiler_flags
from preflight_admission import run_preflight
from run_experiment_rounds import command_run


def replay(audit_path, output, planner, workers=4):
    audit_path, output, planner = Path(audit_path), Path(output), Path(planner).resolve()
    audit = json.loads(audit_path.read_text())
    batch = Path(audit['batch'])
    if output.resolve().is_relative_to(batch.resolve()):
        raise ValueError('Replay output must be outside the original batch')
    if not 1 <= workers <= 4:
        raise ValueError('Offline replay is limited to 1..4 compiler workers')
    output.mkdir(parents=True, exist_ok=False)
    protocol = json.loads((batch / 'protocol.json').read_text())
    runtime = batch / 'runtime'
    compiler_log = runtime / 'coverage-matched-build.log'
    template = compiler_template(compiler_log)
    if not has_required_compiler_flags(template, protocol['cfg_flags']):
        raise ValueError('Frozen compiler flags mismatch')
    evidence = protocol['runtime_evidence']
    for key in ('shared_manifest', 'shared_lock', 'compiler'):
        record = evidence[key]
        if fingerprint(record['path'])['sha256'] != record['sha256']:
            raise ValueError('Frozen runtime changed: ' + key)
    if Path(template[0]).resolve() != Path(evidence['compiler']['path']).resolve():
        raise ValueError('Replay compiler differs from the frozen runtime')
    env = clean_env()
    cpus = sorted(os.sched_getaffinity(0))[-8:]
    implementation = output / 'implementation'
    implementation.mkdir()
    for name in ('admission_diagnostics.py', 'preflight_admission.py',
                 'replay_admission_preflight.py', 'launch_controlled_round.py',
                 'run_experiment_rounds.py', 'coverage_snapshot.py'):
        shutil.copyfile(Path(__file__).with_name(name), implementation / name)
    context = {'protocol': fingerprint(batch / 'protocol.json'),
               'planner': fingerprint(planner), 'compiler_log': fingerprint(compiler_log),
               'implementation': {path.name: fingerprint(path) for path in implementation.iterdir()}}
    atomic_json(output / 'input-manifest.json', {'audit': fingerprint(audit_path), 'context': context,
                                               'workers': workers, 'model_requests': 0})

    def one(target):
        original = Path(target['generation']['path'])
        if fingerprint(original) != target['generation']:
            raise ValueError('Audited generation record changed: ' + str(original))
        plan = original.parent / 'emitted/plan.json'
        if not plan.exists():
            return {**{k: target[k] for k in ('round', 'crate', 'slot', 'accepted')},
                    'preflight': {'status': 'infrastructure_failed',
                                  'diagnosis': {'code': 'missing_plan_evidence'}}}
        # emitted/plan.json is a SequencePlan; planner CLI expects PlanResult.
        directory = output / f"round-{target['round']:02d}" / target['crate'] / str(target['slot'])
        directory.mkdir(parents=True)
        plan_copy = directory / 'input-plan.json'
        atomic_json(plan_copy, {'status': 'planned', 'plan': json.loads(plan.read_text())})

        def emit(plan_path, work, timeout):
            result = command_run([str(planner), 'emit', str(plan_path), str(work / 'emitted')],
                                 work, env, work / 'emit.log', timeout)
            if result['returncode'] != 0:
                raise RuntimeError('Planner emit failed; see emit.log')
            return work / 'emitted/src/main.rs'

        def compile_core(source, binary, timeout):
            command = []
            index = 0
            while index < len(template):
                token = template[index]
                if token == '--crate-name':
                    command += [token, f"preflight_{target['crate'].replace('-', '_')}_{target['slot']}"]
                    index += 2
                    continue
                if token == '--out-dir':
                    index += 2
                    continue
                if token == '-C' and index+1 < len(template) and template[index+1].startswith('incremental='):
                    index += 2
                    continue
                if token.startswith('-Cincremental='):
                    index += 1
                    continue
                if token == 'src/main.rs': token = str(source)
                if token.startswith('--emit='): token = '--emit=link'
                command.append(token)
                index += 1
            command += ['-o', str(binary)]
            return command_run(command, runtime / 'project', env,
                               binary.with_suffix('.compile.log'), timeout, cpus)

        def probe(plan_path, binary, report, timeout):
            return command_run(['prlimit', '--as=1073741824', '--', str(planner),
                                'probe', str(plan_path), str(binary), str(report)],
                               report.parent, dict(env, LLVM_PROFILE_FILE='/dev/null'),
                               report.with_suffix('.log'), timeout, cpus)

        result = run_preflight(plan_copy, directory / 'core-preflight', emit, compile_core,
                               probe, context)
        return {**{k: target[k] for k in ('round', 'crate', 'slot', 'accepted')},
                'original_plan': fingerprint(plan), 'preflight': result}

    started = time.monotonic()
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(one, audit['targets']):
            rows.append(row)
            atomic_json(output / 'progress.json', {'completed': len(rows),
                'total': len(audit['targets']), 'model_requests': 0,
                'statuses': dict(Counter(r['preflight']['status'] for r in rows))})
    result = {'schema': 'graft-paired-preflight-replay-v2', 'targets': rows,
              'total': len(rows), 'elapsed_seconds': time.monotonic() - started,
              'model_requests': 0, 'fuzz_jobs': 0,
              'preflight_statuses': dict(Counter(r['preflight']['status'] for r in rows)),
              'old_admitted': sum(r['accepted'] for r in rows),
              'old_admitted_preflight_passed': sum(r['accepted'] and r['preflight']['status'] == 'passed' for r in rows),
              'old_failed_preflight_rejected': sum(not r['accepted'] and r['preflight']['status'] == 'rejected' for r in rows),
              'note': 'Core preflight is a generation gate, not final admission. No semantic changes or model calls.'}
    atomic_json(output / 'paired-admission-report.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--planner', required=True, type=Path)
    parser.add_argument('--workers', default=4, type=int)
    args = parser.parse_args()
    result = replay(args.audit, args.output, args.planner, args.workers)
    print(json.dumps({k: v for k, v in result.items() if k != 'targets'}, ensure_ascii=False))
