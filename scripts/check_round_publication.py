#!/usr/bin/env python3
"""Exercise the real TeX round publisher on a temporary copy using synthetic data.

No model/fuzz jobs run, and the actual paper/data are untouched. The test PDF is
discarded with the temporary directory; only an explicitly labeled check is saved.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from coverage_snapshot import file_hash, load
from run_experiment_rounds import publish, save, summarize


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol', type=Path, required=True)
    p.add_argument('--paper', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    protocol = load(a.protocol)
    protocol['_file_sha256'] = file_hash(a.protocol)
    with tempfile.TemporaryDirectory(prefix='graft-publication-check-') as temp:
        root = Path(temp)
        paper, batch = root/'paper', root/'batch'
        shutil.copytree(a.paper, paper, ignore=shutil.ignore_patterns('.git', 'out', 'revisions', 'scripts', '__pycache__'))
        universes = {crate: {'apis': [{'api_id': 'synthetic-test-only', 'public_name_body_definition': True}]}
            for crate in protocol['versions']}
        synthetic = [{'method': method, 'crate': crate, 'slot': i, 'status': 'generation_failed'}
            for method in protocol['methods'] for crate in protocol['versions'] for i in range(protocol['target_slots'])]
        summary = summarize(protocol, synthetic, universes, 1)
        summary['scope'] = 'synthetic publisher integration test only, never an experiment'
        save(batch/'round-01/summary.json', summary)
        publish(protocol, batch, paper, 1, summary)
        publication = load(paper/'revisions/short-budget/round-01/publication.json')
        log = (paper/'out/short-budget/round-01/main.log').read_text(errors='replace')
        invalid = ['There were undefined references', 'Missing character:']
        if any(item in log for item in invalid):
            raise ValueError('publication check has undefined references or missing characters')
        original = a.paper/'chapters/generated/short-budget.tex'
        report = {'scope': 'synthetic publisher integration test only; actual paper/data unchanged',
            'passed': True, 'configurations_tested': len(protocol['methods']),
            'csv_rows': len(synthetic)//protocol['target_slots'], 'pdf_build_returncode': publication['build']['returncode'],
            'actual_include_sha256': file_hash(original), 'test_pdf_discarded': True}
        save(a.output, report)
        print(json.dumps(report))


if __name__ == '__main__':
    main()
