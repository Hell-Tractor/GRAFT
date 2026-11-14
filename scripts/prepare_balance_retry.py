#!/usr/bin/env python3
"""Archive only balance/concurrency failures, leaving unrelated outcomes untouched."""
import argparse
import fcntl
import json
import re
import shutil
from collections import Counter
from pathlib import Path

COST_REASONS = {'DeepSeek HTTP 402', 'DeepSeek HTTP 429',
                'shared first response backend failed'}
COST_TEXT = re.compile(r'HTTP[: ]*(?:402|429)\b|\b402 Payment Required\b|'
                       r'\bInsufficient Balance\b|\bconcurrency limit\b', re.IGNORECASE)


def inside(path, root):
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def cost_target(directory):
    outcome = directory/'outcome.json'
    if outcome.exists():
        try:
            if json.loads(outcome.read_text()).get('reason') in COST_REASONS:
                return True
        except (OSError, ValueError):
            pass
    for response in directory.glob('response-*.json'):
        try:
            if json.loads(response.read_text()).get('http_status') in (402, 429):
                return True
        except (OSError, ValueError):
            pass
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--archive-name', default='20261110-balance-renewal')
    args = parser.parse_args()
    root = args.root.resolve()
    with (root/'.run.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('refusing repair while the experiment lock is held')
        prep = root/'preparation/round-01'
        targets = sorted({path.parent for path in prep.glob('*/*/*/outcome.json')
                          if cost_target(path.parent)} |
                         {path.parent for path in prep.glob('*/*/*/response-*.json')
                          if cost_target(path.parent)})
        deep_root = root/'deepsurf-native/round-01'
        deep_crates = []
        for log in sorted(deep_root.glob('*/generation.log')):
            text = log.read_text(errors='replace')[-200000:]
            if COST_TEXT.search(text):
                deep_crates.append(log.parent.name)
        if not targets and not deep_crates:
            raise SystemExit('no balance/concurrency failures found')
        archive = root/'retry-archive'/args.archive_name/'round-01'
        if archive.exists():
            raise SystemExit('archive already exists: '+str(archive))
        manifest = {
            'scope': 'only HTTP 402/429 and their shared no-repair failures',
            'llm_targets': [str(path.relative_to(root)) for path in targets],
            'deepsurf_crates': deep_crates,
            'method_counts': dict(Counter(path.parts[-3] for path in targets)),
            'source_round': 1,
            'archive': str(archive),
            'apply': args.apply,
        }
        print(json.dumps({k:v for k,v in manifest.items() if k!='llm_targets'}, indent=2))
        print('llm_target_count', len(targets))
        if not args.apply:
            return
        snapshots = archive/'snapshots'
        snapshots.mkdir(parents=True)
        for relative in ('progress.json', 'graft-generation-latest.json',
                         'all-method-generation-latest.json', 'round-01/summary.json',
                         'round-01/all-method-generation.json'):
            source = root/relative
            if source.exists():
                destination = snapshots/relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        publication = Path('/home/cym/workspace/GRAFT-2.0/GRAFTPaper/revisions/short-budget/round-01')
        if publication.exists():
            shutil.copytree(publication, snapshots/'paper-publication-round-01')
        moved_llm, moved_deep, moved_results = [], [], []
        for source in targets:
            if not inside(source, root):
                raise RuntimeError('target escaped experiment root: '+str(source))
            destination = archive/'preparation'/source.relative_to(prep)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved_llm.append(str(source.relative_to(root)))
        for crate in deep_crates:
            source = deep_root/crate
            if source.exists():
                destination = archive/'deepsurf-native'/crate
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved_deep.append(crate)
            task_root = prep/'deepSURF'/crate
            if task_root.exists():
                destination = archive/'preparation-deepsurf'/crate
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(task_root), str(destination))
        for source in sorted((root/'round-01').glob('*/*/*')):
            key = (source.parts[-3], source.parts[-2], source.parts[-1])
            if any((prep.joinpath(*key)) == path for path in targets):
                destination = archive/'round-results'/source.relative_to(root/'round-01')
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved_results.append(str(source.relative_to(root)))
        manifest.update(status='prepared', moved_llm=moved_llm,
                        moved_deepsurf_crates=moved_deep, moved_round_results=moved_results)
        (archive/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':
    main()
