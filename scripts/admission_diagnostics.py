"""Admission evidence and repair feedback. No model, credential or network access."""
from collections import Counter
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import time


EVIDENCE_NAMES = (
    'normalize.log', 'validity.json', 'candidate.compile.log',
    'original-wrapper.compile.log', 'original-wrapper-probe.log',
    'original-wrapper-probe.json', 'replay.compile.log', 'probe.log',
    'wrapper-probe.json', 'input-probes.json', 'build-evidence.json',
    'fuzz.compile.log', 'outcome.json',
)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def fingerprint(path):
    path = Path(path)
    return {'path': str(path.resolve()),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def compiler_errors(path):
    if not Path(path).exists():
        return []
    errors = []
    for line in Path(path).read_text(errors='replace').splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            if line.lstrip().startswith(('error', 'help:', 'note:')):
                errors.append(line.strip())
            continue
        if isinstance(record, dict) and record.get('level') == 'error':
            code = (record.get('code') or {}).get('code', '')
            errors.append(code + ': ' + record.get('message', ''))
            errors.extend(c['message'] for c in record.get('children', []) if c.get('message'))
    return errors


def diagnose(directory):
    """Classify this evidence snapshot, not stale logs from a previous attempt."""
    directory = Path(directory)
    normalization = directory / 'normalize.log'
    text = normalization.read_text(errors='replace') if normalization.exists() else ''
    if 'planned core AST changed' in text:
        return {'stage': 'normalization', 'code': 'core_changed', 'message': text.strip()}
    validity = read_json(directory / 'validity.json')
    if validity and not validity.get('accepted'):
        return {'stage': 'normalization', 'code': 'contract_rejected',
                'message': '; '.join(validity.get('reasons', []))}
    for name in ('candidate.compile.log', 'original-wrapper.compile.log',
                 'replay.compile.log', 'fuzz.compile.log'):
        errors = compiler_errors(directory / name)
        if errors:
            if any(any(marker in e for marker in ("can't find crate", 'E0463:', 'E0514:',
                        'compiled by an incompatible version', 'No space left on device')) for e in errors):
                code = 'infrastructure_error'
            else:
                code = ('dependency_identity_mismatch' if any(
                    'multiple different versions' in e for e in errors) else 'core_compile_error')
            return {'stage': name.removesuffix('.compile.log'), 'code': code,
                    'message': '\n'.join(errors)}
    for name in ('original-wrapper-probe.json', 'wrapper-probe.json'):
        report = read_json(directory / name)
        if not report or report.get('bounded_probe_passed'):
            continue
        samples = report.get('samples', [])
        failed = [s for s in samples if s.get('timed_out') or s.get('exit_code') != 0]
        stderr = '\n'.join(s.get('stderr', '') for s in samples)
        if any(s.get('timed_out') for s in failed):
            code = 'probe_timeout'
        elif 'memory allocation' in stderr or 'out of memory' in stderr:
            code = 'resource_limit'
        elif 'panicked' in stderr:
            code = 'runtime_panic'
        elif failed:
            code = 'probe_process_failed'
        elif not report.get('reached_inputs'):
            code = 'target_not_reached'
        elif report.get('scalar_input_influence') == 'not_observed':
            code = 'input_influence_unobserved'
        else:
            code = 'probe_rejected'
        # Args are standalone byte arguments, not fuzzer corpus paths.
        return {'stage': name.removesuffix('.json'), 'code': code,
                'message': stderr.strip() or code,
                'samples': [{k: s.get(k) for k in ('args', 'exit_code', 'timed_out',
                            'target_reached', 'scalar_arguments', 'stderr')} for s in samples]}
    outcome = read_json(directory / 'outcome.json')
    return {'stage': 'other', 'code': 'unknown',
            'message': outcome.get('reason', 'No conclusive stage evidence') if outcome else
                       'No conclusive stage evidence'}


def begin_evidence(directory):
    """Rotate live diagnostics before validation; wall-clock mtimes are unreliable.

    Earlier immutable seals remain in validation-attempts. Preserve superseded
    live files too, including evidence from a preparation interrupted mid-attempt.
    """
    directory = Path(directory)
    paths = [directory / name for name in EVIDENCE_NAMES]
    paths += sorted(directory.glob('input-*.log'))
    paths = [p for p in paths if p.exists()]
    if paths:
        archive = directory / 'superseded-evidence' / str(time.time_ns())
        archive.mkdir(parents=True, exist_ok=False)
        for path in paths:
            path.rename(archive / path.name)


def capture_attempt(directory, index, source, accepted, identity=None):
    """Seal only evidence written by this attempt; never overwrite an earlier seal."""
    directory = Path(directory)
    target = directory / 'validation-attempts' / str(index)
    target.mkdir(parents=True, exist_ok=False)
    artifacts = {}
    for name in EVIDENCE_NAMES:
        path = directory / name
        if path.exists():
            shutil.copyfile(path, target / name)
            artifacts[name] = fingerprint(target / name)
    for path in sorted(directory.glob('input-*.log')):
        shutil.copyfile(path, target / path.name)
        artifacts[path.name] = fingerprint(target / path.name)
    diagnosis = {'stage': 'complete', 'code': 'accepted', 'message': 'All admission gates passed'} \
        if accepted else diagnose(target)
    record = {'schema': 'graft-admission-attempt-v2', 'index': index,
              'identity': identity or {}, 'accepted': bool(accepted),
              'source': fingerprint(source), 'diagnosis': diagnosis, 'artifacts': artifacts}
    atomic_json(target / 'evidence.json', record)
    # One target can be updated from different admission paths. Lock append writes.
    with (directory / 'admission-events.jsonl').open('a') as out:
        fcntl.flock(out, fcntl.LOCK_EX)
        out.write(json.dumps(record, ensure_ascii=False) + '\n')
        out.flush()
    return record


def repair_feedback(record):
    """Plain error/note lines survive the existing cost-policy error compactor."""
    diagnosis = record['diagnosis']
    lines = ['error: [' + diagnosis['code'] + '] ' + diagnosis['stage']]
    lines += ['note: ' + line for line in diagnosis.get('message', '').splitlines()[:16]]
    for sample in diagnosis.get('samples', []):
        if not sample.get('target_reached') or sample.get('exit_code') != 0:
            lines.append('note: failing byte args=' + json.dumps(sample.get('args')) +
                         ' exit=' + str(sample.get('exit_code')) +
                         ' target_reached=' + str(sample.get('target_reached')))
    return '\n'.join(lines)


def audit_batch(batch, output, method='GRAFT'):
    """Read-only, zero-cost audit of historical attempts. Never execute old source."""
    batch, output = Path(batch), Path(output)
    if output.resolve().is_relative_to(batch.resolve()):
        raise ValueError('Audit output must be outside the frozen source batch')
    records = []
    for path in sorted((batch / 'preparation').glob('round-*/*/*/*/generation.json')):
        generation = read_json(path)
        if not generation or generation.get('method') != method or not generation.get('finished'):
            continue
        directory = path.parent
        sealed = sorted(directory.glob('validation-attempts/*/evidence.json'),
                        key=lambda p: int(p.parent.name))
        record = {'round': generation['round'], 'method': method,
                  'crate': generation['crate'], 'slot': generation['slot'],
                  'generation': fingerprint(path), 'accepted': bool(generation.get('accepted')),
                  'attempts': len(generation.get('attempts', [])),
                  'evidence_scope': 'sealed_attempts' if sealed else 'latest_files_only',
                  'diagnosis': (read_json(sealed[-1])['diagnosis'] if sealed else
                      {'code': 'accepted', 'stage': 'complete', 'message': 'Previously admitted'}
                      if generation.get('accepted') else diagnose(directory))}
        # Legacy overwrites cannot be reconstructed honestly from the last log.
        record['earlier_attempt_evidence_complete'] = len(sealed) == record['attempts']
        records.append(record)
    result = {'schema': 'graft-admission-audit-v2', 'created_unix_seconds': time.time(),
              'batch': str(batch.resolve()), 'method': method, 'completed': len(records),
              'accepted': sum(r['accepted'] for r in records),
              'final_causes': dict(Counter(r['diagnosis']['code'] for r in records)),
              'missing_earlier_attempt_evidence': sum(not r['earlier_attempt_evidence_complete']
                                                      for r in records), 'targets': records}
    atomic_json(output, result)
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--method', default='GRAFT')
    args = parser.parse_args()
    result = audit_batch(args.batch, args.output, args.method)
    print(json.dumps({k: v for k, v in result.items() if k != 'targets'}, ensure_ascii=False))
