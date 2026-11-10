"""Versioned, fail-closed local core preflight. Callbacks never invoke a model."""
import hashlib
import json
from pathlib import Path
import time

from admission_diagnostics import atomic_json, diagnose, fingerprint, read_json


def read_profile(path):
    profile = json.loads(Path(path).read_text())
    if profile.get('schema') != 'graft-admission-profile-v2':
        raise ValueError('unsupported admission profile')
    if profile.get('core_preflight') is not True or profile.get('gate') != 'strict-three-input-v1':
        raise ValueError('stage A/B requires the unchanged strict three-input gate')
    if profile.get('assembly_mode') not in ('legacy-full-source','llm-parameter-repair'):
        raise ValueError('unsupported admission assembly mode')
    if profile.get('assembly_mode') == 'llm-parameter-repair' and profile.get('repair_contract') != 'planned-call-v2':
        raise ValueError('parameter repair requires the planned-call-v2 contract')
    if profile.get('replanning') is not False or profile.get('semantics_rules') is not None:
        raise ValueError('semantic rules and replanning are not implemented in stage A/B')
    seconds = profile.get('preflight_wall_seconds')
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 60:
        raise ValueError('preflight wall budget must be in (0, 60] seconds')
    return profile


def run_preflight(plan, directory, emit, compile_core, probe, context, wall_seconds=60):
    """Callbacks: emit(plan, dir), compile(source, binary, timeout), probe(...).

    Cache includes failed deterministic plans, but never infrastructure failures.
    Both successful and rejected evidence must still match all artifact hashes.
    Admission/fuzz are separate: this function can only admit a core to generation.
    """
    plan, directory = Path(plan), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {'plan': fingerprint(plan), 'context': context,
               'wall_seconds': wall_seconds, 'gate': 'strict-three-input-v1'}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    result_path = directory / 'preflight-results.json'
    cached = read_json(result_path)
    if cached and cached.get('cache_key') == key and cached.get('status') in ('passed', 'rejected'):
        artifacts = cached.get('artifacts', {})
        if artifacts and all(Path(a['path']).exists() and fingerprint(a['path']) == a
                             for a in artifacts.values()):
            return dict(cached, cache_hit=True)
    # Each run owns immutable evidence, even after a cache invalidation.
    started = time.monotonic()
    attempt = directory / ('run-' + str(time.time_ns()))
    attempt.mkdir()
    result = {'schema': 'graft-core-preflight-v2', 'cache_key': key,
              'cache_inputs': payload, 'cache_hit': False, 'status': 'infrastructure_failed',
              'artifacts': {}, 'model_requests': 0}

    def remaining():
        seconds = wall_seconds - (time.monotonic() - started)
        if seconds <= 0:
            raise TimeoutError('preflight wall budget exhausted')
        return seconds

    def save_artifact(name, path):
        result['artifacts'][name] = fingerprint(path)

    try:
        source = Path(emit(plan, attempt, remaining()))
        save_artifact('source', source)
        binary = attempt / 'original-wrapper'
        compiled = compile_core(source, binary, remaining())
        result['compile'] = compiled
        log = binary.with_suffix('.compile.log')
        if log.exists():
            save_artifact('compile_log', log)
        if compiled.get('timed_out'):
            raise TimeoutError('core compilation timed out')
        if compiled['returncode'] != 0:
            diagnosis = diagnose(attempt)
            result.update(status='rejected' if diagnosis['code'] in
                          ('core_compile_error', 'dependency_identity_mismatch') else
                          'infrastructure_failed', diagnosis=diagnosis)
        else:
            save_artifact('executable', binary)
            report_path = attempt / 'original-wrapper-probe.json'
            run = probe(plan, binary, report_path, remaining())
            result['probe'] = run
            if report_path.exists():
                save_artifact('probe_report', report_path)
            if run.get('timed_out'):
                raise TimeoutError('core probe command timed out')
            report = read_json(report_path)
            if run['returncode'] == 0 and report and report.get('bounded_probe_passed') is True:
                result.update(status='passed', diagnosis={'stage': 'core-preflight',
                              'code': 'passed', 'message': 'Core passed; full admission still required'})
            elif run['returncode'] == 3 and report and report.get('bounded_probe_passed') is False:
                result.update(status='rejected', diagnosis=diagnose(attempt))
            else:
                result['diagnosis'] = {'stage': 'core-preflight', 'code': 'infrastructure_error',
                                       'message': 'Missing/inconsistent probe report or probe command failed'}
    except Exception as error:
        result.update(status='infrastructure_failed', diagnosis={'stage': 'core-preflight',
                      'code': 'infrastructure_error', 'message': str(error)})
    result['elapsed_seconds'] = time.monotonic() - started
    atomic_json(attempt / 'result.json', result)
    atomic_json(result_path, result)
    return result
