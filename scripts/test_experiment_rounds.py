import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from run_experiment_rounds import (check_tasks, cumulative, execute, file_hash,
    publish, seed_for, summarize, save)
from estimate_experiment_time import estimate, scenarios
from replay_round_coverage import corpus_files
from prepare_round_preflight import review_flags


class ExperimentRoundsTests(unittest.TestCase):
    def setUp(self):
        self.protocol = {'methods': ['GRAFT', 'RPG'], 'versions': {'fixture': '0.1.0'},
            'target_slots': 2, 'seconds_per_target': 300, '_file_sha256': 'sealed',
            'workers_per_target': 1}
        self.universes = {'fixture': {'apis': [
            {'api_id': 'a', 'public_name_body_definition': True},
            {'api_id': 'b', 'public_name_body_definition': True},
            {'api_id': 'review', 'public_surface_review_item': True}]}}

    def results(self):
        return [{'method': method, 'crate': 'fixture', 'slot': slot,
            'status': 'measured', 'covered_api_ids': ['a']}
            for method in self.protocol['methods'] for slot in range(2)]

    def test_round_barriers_cost_more_than_pooled_lower_bound(self):
        s = scenarios(repeats=6)[1]
        s = dict(s, groups=[dict(g, workers=1) for g in s['groups']])
        value = estimate(s, 56, 24, 6, 2, seconds=300, rounds=6)
        self.assertEqual(value['harness_runs'], 17160)
        self.assertAlmostEqual(value['selected_fuzz_lower_bound_hours'], 25.5357142857)
        self.assertEqual(value['selected_feasible_fuzz_hours'], 26)

    def test_seeds_paired_across_methods_but_separate_rounds_and_slots(self):
        values = [seed_for(20261109, r, 'fixture', i) for r in range(1, 7) for i in range(20)]
        self.assertEqual(len(set(values)), len(values))
        self.assertTrue(all(0 < v < 2**32 for v in values))
        self.assertEqual(values[0], seed_for(20261109, 1, 'fixture', 0))

    def test_union_targets_within_round_without_double_count(self):
        result = summarize(self.protocol, self.results(), self.universes, 1)
        self.assertEqual(result['status'], 'complete')
        for row in result['rows']:
            self.assertEqual(row['denominator'], 2)
            self.assertEqual(row['confirmed_covered_lower_bound'], 1)
            self.assertEqual(row['coverage_percent'], 50)

    def test_infrastructure_failure_is_missing_not_zero(self):
        rows = self.results()
        rows[0].update(status='replay_failed', covered_api_ids=[])
        result = summarize(self.protocol, rows, self.universes, 1)
        self.assertEqual(result['status'], 'incomplete')
        self.assertIsNone(result['rows'][0]['coverage_percent'])
        self.assertEqual(result['rows'][0]['confirmed_covered_lower_bound'], 1)
        self.assertEqual(result['rows'][0]['infrastructure_gap_slots'], 1)

    def test_generation_failure_stays_in_fixed_slot_denominator(self):
        rows = self.results()
        rows[0].update(status='generation_failed', covered_api_ids=[])
        result = summarize(self.protocol, rows, self.universes, 1)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['rows'][0]['upstream_failure_slots'], 1)
        self.assertEqual(result['rows'][0]['coverage_percent'], 50)

    def test_missing_slot_cannot_complete_round(self):
        result = summarize(self.protocol, self.results()[1:], self.universes, 1)
        self.assertEqual(result['status'], 'incomplete')

    def test_unknown_coverage_id_rejected(self):
        rows = self.results()
        rows[0]['covered_api_ids'] = ['review']
        with self.assertRaises(ValueError):
            summarize(self.protocol, rows, self.universes, 1)

    def test_average_rounds_instead_of_union_and_sd_not_zero_at_n_one(self):
        first = summarize(self.protocol, self.results(), self.universes, 1)
        values = self.results()
        for row in values:
            row['covered_api_ids'] = ['a', 'b']
        second = summarize(self.protocol, values, self.universes, 2)
        one = cumulative(self.protocol, [first])[0]
        self.assertEqual(one['macro_mean_percent'], 50)
        self.assertIsNone(one['macro_sample_sd'])
        both = cumulative(self.protocol, [first, second])[0]
        self.assertEqual(both['macro_mean_percent'], 75)
        self.assertAlmostEqual(both['macro_sample_sd'], 35.3553390593)

    def test_incomplete_round_excluded_from_mean_but_counted_attempted(self):
        rows = self.results()
        rows[0]['status'] = 'interrupted'
        result = cumulative(self.protocol, [summarize(self.protocol, rows, self.universes, 1)])[0]
        self.assertEqual(result['complete_rounds'], 0)
        self.assertEqual(result['attempted_rounds'], 1)
        self.assertIsNone(result['macro_mean_percent'])

    def test_pending_blocks_launch(self):
        manifest = {'round': 1, 'protocol_sha256': 'sealed', 'tasks': [
            {'method': 'GRAFT', 'crate': 'fixture', 'slot': 0, 'status': 'pending'}]}
        with self.assertRaisesRegex(ValueError, 'unprepared'):
            check_tasks(self.protocol, manifest, 1)

    def test_replay_corpus_requires_afl_queue_and_deduplicates_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initial = root/'initial'
            initial.mkdir()
            (initial/'zero').write_bytes(b'zero')
            with self.assertRaisesRegex(ValueError, 'no queue'):
                corpus_files(initial, root/'afl')
            queue = root/'afl/default/queue'
            queue.mkdir(parents=True)
            (queue/'id:000000,orig:zero').write_bytes(b'zero')
            (queue/'id:000001').write_bytes(b'other')
            (queue/'README.txt').write_text('not a testcase')
            corpus = corpus_files(initial, root/'afl')
            self.assertEqual(len(corpus), 2)
            self.assertEqual(sorted(map(len, corpus.values())), [1, 2])

    def test_unsafe_screen_is_not_confused_by_known_replay_file_read(self):
        self.assertEqual(review_flags('let data = std::fs::read(path).unwrap();'), [])
        self.assertTrue(review_flags('let p = x as *mut u8;'))
        self.assertTrue(review_flags('std::fs::write(path, data);'))

    def test_no_silent_drop_or_duplicate_of_failure_slots(self):
        with tempfile.TemporaryDirectory() as temp:
            evidence = Path(temp) / 'failure.json'
            evidence.write_text('{"status":"generation_failed"}')
            tasks = [dict(r, status='generation_failed', evidence={
                'path': str(evidence), 'sha256': file_hash(evidence)}) for r in self.results()]
            manifest = {'round': 1, 'protocol_sha256': 'sealed', 'tasks': tasks}
            self.assertEqual(len(check_tasks(self.protocol, manifest, 1)), 4)
            manifest['tasks'] = tasks[:-1]
            with self.assertRaisesRegex(ValueError, 'missing'):
                check_tasks(self.protocol, manifest, 1)
            manifest['tasks'] = tasks + [tasks[0]]
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                check_tasks(self.protocol, manifest, 1)
            evidence.write_text('changed')
            manifest['tasks'] = tasks
            with self.assertRaisesRegex(ValueError, 'changed'):
                check_tasks(self.protocol, manifest, 1)

    def test_interrupted_run_not_restarted_with_another_300_seconds(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'started.json').write_text('{}')
            task = {'method': 'GRAFT', 'crate': 'fixture', 'slot': 0, 'status': 'ready'}
            result = execute(task, self.protocol, 1, directory, [], {})
            self.assertEqual(result['status'], 'interrupted')
            self.assertEqual(execute(task, self.protocol, 1, directory, [], {}), result)
            task['version'] = 'new-version'
            with self.assertRaisesRegex(ValueError, 'differs'):
                execute(task, self.protocol, 1, directory, [], {})

    def test_paper_build_failure_restores_include_and_does_not_publish_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch, paper = root/'batch', root/'paper'
            report = summarize(self.protocol, self.results(), self.universes, 1)
            save(batch/'round-01/summary.json', report)
            include = paper/'chapters/generated/short-budget.tex'
            include.parent.mkdir(parents=True)
            include.write_text('previous valid paper data')
            protocol = dict(self.protocol, rounds=6, tectonic='unit-test-only', tectonic_cache=temp)
            with patch('run_experiment_rounds.command_run', return_value={'returncode': 1, 'timed_out': False}):
                with self.assertRaisesRegex(RuntimeError, 'paper build failed'):
                    publish(protocol, batch, paper, 1, report)
            self.assertEqual(include.read_text(), 'previous valid paper data')
            self.assertFalse((paper/'revisions/short-budget/latest.json').exists())

    def test_successful_round_publishes_csv_stats_pdf_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            batch, paper = root/'batch', root/'paper'
            report = summarize(self.protocol, self.results(), self.universes, 1)
            save(batch/'round-01/summary.json', report)
            protocol = dict(self.protocol, rounds=6, tectonic='unit-test-only', tectonic_cache=temp)
            def unit_build(command, *args):
                out = Path(command[command.index('--outdir')+1])
                (out/'main.pdf').write_bytes(b'unit-test placeholder, not a scientific artifact')
                return {'returncode': 0, 'timed_out': False}
            with patch('run_experiment_rounds.command_run', side_effect=unit_build):
                publish(protocol, batch, paper, 1, report)
            directory = paper/'revisions/short-budget/round-01'
            self.assertIn('coverage_percent', (directory/'coverage.csv').read_text())
            self.assertEqual(json.loads((directory/'statistics.json').read_text())['through_round'], 1)
            self.assertEqual(json.loads((paper/'revisions/short-budget/latest.json').read_text())['round'], 1)
            self.assertEqual(json.loads((directory/'publication.json').read_text())['pdf_sha256'],
                file_hash(paper/'out/short-budget/round-01/main.pdf'))

    def test_variant_publication_preserves_legacy_results_and_manuscript_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);batch,paper=root/'batch',root/'paper'
            report=summarize(self.protocol,self.results(),self.universes,1)
            save(batch/'round-01/summary.json',report)
            legacy=paper/'revisions/short-budget/latest.json'
            save(legacy,{'previous':'legacy'})
            include=paper/'chapters/generated/short-budget.tex'
            include.parent.mkdir(parents=True);include.write_text('previous paper selection')
            protocol=dict(self.protocol,rounds=6,tectonic='unit-test-only',tectonic_cache=temp,
                          publication_namespace='admission-v2/batch-test')
            def build(command,*args):
                out=Path(command[command.index('--outdir')+1])
                (out/'main.pdf').write_bytes(b'unit-test placeholder')
                self.assertNotEqual(include.read_text(),'previous paper selection')
                return {'returncode':0,'timed_out':False}
            with patch('run_experiment_rounds.command_run',side_effect=build):
                publish(protocol,batch,paper,1,report)
            self.assertEqual(include.read_text(),'previous paper selection')
            self.assertEqual(json.loads(legacy.read_text()),{'previous':'legacy'})
            self.assertTrue((paper/'revisions/admission-v2/batch-test/round-01/publication.json').exists())
            self.assertTrue((paper/'out/admission-v2/batch-test/round-01/main.pdf').exists())
            self.assertTrue((paper/'chapters/generated/admission-v2/batch-test/results.tex').exists())

    def test_publication_rejects_traversal_namespace(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            protocol=dict(self.protocol,publication_namespace='../other-paper')
            with self.assertRaisesRegex(ValueError,'namespace'):
                publish(protocol,root/'batch',root/'paper',1,{})


if __name__ == '__main__':
    unittest.main()
