import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from admission_diagnostics import (atomic_json, audit_batch, begin_evidence, capture_attempt,
                                   diagnose, repair_feedback)
from model_cost_control import compact_diagnostics
from preflight_admission import read_profile, run_preflight
from launch_controlled_round import Engine


class DiagnosticsTests(unittest.TestCase):
    def test_probe_failure_reaches_budgeted_repair_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory / 'attempt.rs'
            source.write_text('fn main() {}')
            begin_evidence(directory)
            atomic_json(directory / 'original-wrapper-probe.json', {
                'bounded_probe_passed': False, 'reached_inputs': 2,
                'samples': [{'args': ['1'], 'exit_code': None, 'timed_out': False,
                             'target_reached': False,
                             'stderr': 'memory allocation of 72340172838076673 bytes failed'}]})
            evidence = capture_attempt(directory, 0, source, False)
            feedback = compact_diagnostics(repair_feedback(evidence), 1400)
            self.assertIn('resource_limit', feedback)
            self.assertIn('72340172838076673', feedback)
            self.assertIn('args=["1"]', feedback)

    def test_stale_probe_is_not_reused_after_normalization_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory / 'attempt.rs'; source.write_text('fn main() {}')
            stale = directory / 'original-wrapper-probe.json'
            atomic_json(stale, {'bounded_probe_passed': False, 'samples': []})
            os.utime(stale, ns=(1, 1))
            begin_evidence(directory)
            (directory / 'normalize.log').write_text('planned core AST changed: input decoder')
            evidence = capture_attempt(directory, 1, source, False)
            self.assertNotIn(stale.name, evidence['artifacts'])
            self.assertEqual(evidence['diagnosis']['code'], 'core_changed')
            with self.assertRaises(FileExistsError):
                capture_attempt(directory, 1, source, False)

    def test_saved_diagnostics_survive_repair_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory / 'attempt.rs'; source.write_text('fn main() {}')
            (directory / 'normalize.log').write_text('planned core AST changed: original')
            record = capture_attempt(directory, 0, source, False)
            (directory / 'normalize.log').write_text('new log')
            saved = Path(record['artifacts']['normalize.log']['path']).read_text()
            self.assertIn('original', saved)

    def test_legacy_audit_discloses_missing_history_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / 'batch'
            directory = batch / 'preparation/round-01/GRAFT/bytes/0'
            directory.mkdir(parents=True)
            atomic_json(directory / 'generation.json', {'round': 1, 'method': 'GRAFT',
                'crate': 'bytes', 'slot': 0, 'finished': True, 'accepted': False,
                'attempts': [{}, {}]})
            (directory / 'normalize.log').write_text('planned core AST changed')
            before = {p: p.read_bytes() for p in directory.iterdir()}
            result = audit_batch(batch, Path(tmp) / 'audit.json')
            self.assertEqual(result['missing_earlier_attempt_evidence'], 1)
            self.assertEqual(result['final_causes'], {'core_changed': 1})
            self.assertEqual(before, {p: p.read_bytes() for p in directory.iterdir()})
            with self.assertRaises(ValueError):
                audit_batch(batch, batch / 'audit.json')

    def test_unknown_is_not_fabricated_as_a_core_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(diagnose(tmp)['code'], 'unknown')


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = self.root / 'plan.json'; self.plan.write_text('{"target":"fixed"}')
        self.emit_calls = 0; self.compile_calls = 0

    def emit(self, plan, directory, timeout):
        self.assertGreater(timeout, 0)
        self.emit_calls += 1
        source = directory / 'main.rs'; source.write_text('fn main() {}')
        return source

    def compile(self, source, binary, timeout):
        self.compile_calls += 1; binary.write_bytes(b'fake executable')
        binary.with_suffix('.compile.log').write_text('')
        return {'returncode': 0, 'timed_out': False}

    def probe(self, plan, binary, report, timeout):
        atomic_json(report, {'bounded_probe_passed': True, 'samples': []})
        return {'returncode': 0, 'timed_out': False}

    def run_core(self, **overrides):
        kwargs = dict(plan=self.plan, directory=self.root / 'checks', emit=self.emit,
                      compile_core=self.compile, probe=self.probe,
                      context={'compiler': 'frozen-a'})
        kwargs.update(overrides)
        return run_preflight(**kwargs)

    def test_success_is_cached_but_binary_and_context_changes_invalidate(self):
        first = self.run_core()
        self.assertEqual(first['status'], 'passed')
        self.assertTrue(self.run_core()['cache_hit'])
        self.assertEqual(self.compile_calls, 1)
        Path(first['artifacts']['executable']['path']).write_bytes(b'tampered')
        self.assertFalse(self.run_core()['cache_hit'])
        self.run_core(context={'compiler': 'frozen-b'})
        self.assertEqual(self.compile_calls, 3)

    def test_deterministic_rejection_and_failure_input_are_preserved(self):
        def failed_probe(plan, binary, report, timeout):
            atomic_json(report, {'bounded_probe_passed': False, 'reached_inputs': 0,
                'samples': [{'args': ['0'], 'exit_code': 101, 'timed_out': False,
                             'stderr': 'thread main panicked: invalid date'}]})
            return {'returncode': 3, 'timed_out': False}
        result = self.run_core(probe=failed_probe)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['model_requests'], 0)
        self.assertEqual(result['diagnosis']['code'], 'runtime_panic')
        self.assertTrue(self.run_core(probe=failed_probe)['cache_hit'])

    def test_missing_report_and_timeout_are_infrastructure_not_algorithm_failure(self):
        broken = lambda *args: {'returncode': 0, 'timed_out': False}
        result = self.run_core(probe=broken)
        self.assertEqual(result['status'], 'infrastructure_failed')
        self.assertFalse(self.run_core(probe=broken)['cache_hit'])
        result = self.run_core(compile_core=lambda *args: {'returncode': -1, 'timed_out': True})
        self.assertEqual(result['diagnosis']['code'], 'infrastructure_error')

    def test_missing_runtime_library_is_not_cached_as_a_bad_plan(self):
        def missing_library(source, binary, timeout):
            binary.with_suffix('.compile.log').write_text(json.dumps({
                'level':'error','code':{'code':'E0463'},'message':"can't find crate bytes"}))
            return {'returncode':1,'timed_out':False}
        result=self.run_core(compile_core=missing_library)
        self.assertEqual(result['status'],'infrastructure_failed')
        self.assertEqual(result['diagnosis']['code'],'infrastructure_error')
        self.assertFalse(self.run_core(compile_core=missing_library)['cache_hit'])

    def test_deadline_is_checked_between_phases(self):
        def slow_emit(plan, directory, timeout):
            source = self.emit(plan, directory, timeout)
            time.sleep(.01)
            return source
        result = self.run_core(emit=slow_emit, wall_seconds=.001)
        self.assertEqual(result['status'], 'infrastructure_failed')
        self.assertEqual(self.compile_calls, 0)

    def test_policy_cannot_silently_relax_gate(self):
        policy = Path(__file__).resolve().parents[1] / 'experiments/admission-v2/stage-ab.json'
        self.assertTrue(read_profile(policy)['core_preflight'])
        value = json.loads(policy.read_text()); value['gate'] = 'any-input-passes'
        output = self.root / 'bad-policy.json'; atomic_json(output, value)
        with self.assertRaises(ValueError): read_profile(output)


class EngineIntegrationTests(unittest.TestCase):
    """Exercise Engine.model dispatch without credentials, HTTP or fuzz workers."""
    def make_engine(self, directory, preflight_status):
        engine = Engine.__new__(Engine)
        engine.root = directory; engine.round_name = 'round-01'; engine.number = 1
        engine.lock = threading.RLock(); engine.worker_local = threading.local()
        engine.cost_policy = {'max_calls_per_target': 2}
        engine.admission_profile = {'core_preflight': True}
        engine.preflight = Mock(return_value={'status': preflight_status})
        engine.update = lambda: None
        engine.tasks = {}; engine.results = {}; engine.source_index = {}
        engine.identities = {('GRAFT', 'bytes', 0): {'method': 'GRAFT', 'crate': 'bytes', 'slot': 0},
                             ('no-repair', 'bytes', 0): {'method': 'no-repair', 'crate': 'bytes', 'slot': 0}}
        engine.generation = {('GRAFT', 'bytes', 0): {'method': 'GRAFT', 'crate': 'bytes', 'slot': 0,
            'queued_unix_seconds': time.time(), 'attempts': [], 'finished': False, 'accepted': False}}
        engine.request = Mock(side_effect=AssertionError('No paid request is permitted'))
        return engine

    def test_rejected_core_never_calls_model_and_keeps_paired_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp), 'rejected')
            engine.model('GRAFT', 'bytes', 0, 'prompt', plan=Path(tmp) / 'plan.json', paired=True)
            engine.request.assert_not_called()
            self.assertEqual(engine.tasks[('GRAFT', 'bytes', 0)]['status'], 'planning_unknown')
            self.assertEqual(engine.tasks[('no-repair', 'bytes', 0)]['status'], 'planning_unknown')
            self.assertTrue(engine.generation[('GRAFT', 'bytes', 0)]['finished'])
            self.assertEqual(engine.generation[('GRAFT', 'bytes', 0)]['attempts'], [])

    def test_preflight_infrastructure_failure_is_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp), 'infrastructure_failed')
            engine.model('GRAFT', 'bytes', 0, 'prompt', plan=Path(tmp) / 'plan.json')
            engine.request.assert_not_called()
            self.assertEqual(engine.tasks[('GRAFT', 'bytes', 0)]['status'], 'infrastructure_failed')

    def test_compile_queue_wait_is_inside_preflight_wall_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine.__new__(Engine)
            engine.templates={'coverage':['/bin/true','src/main.rs']}
            engine.runtime=Path(tmp);engine.env={}
            engine.compile_slots=threading.Semaphore(0)
            source=Path(tmp)/'main.rs';source.write_text('fn main() {}')
            with patch('launch_controlled_round.command_run') as run:
                with self.assertRaisesRegex(TimeoutError,'queue budget'):
                    engine.compile(source,Path(tmp)/'target','coverage',
                                   timeout=.001,include_queue_wait=True)
                run.assert_not_called()

    def test_each_repair_uses_its_own_evidence_and_actual_call_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp), 'passed')
            prompts = []
            def request(prompt, directory, index):
                prompts.append(prompt)
                source = directory / f'attempt-{index}.rs'; source.write_text(f'// {index}\nfn main() {{}}')
                atomic_json(directory / f'response-{index}.json', {'latency_seconds': 0})
                return source, {}
            def normalize(source, directory, plan, baseline):
                (directory / 'normalize.log').write_text('planned core AST changed: concrete reason')
                return False, {'returncode': 1}
            engine.request = Mock(side_effect=request); engine.normalize = normalize
            engine.model('GRAFT', 'bytes', 0, 'base prompt', plan=Path(tmp) / 'plan.json')
            self.assertEqual(engine.request.call_count, 2)
            self.assertIn('concrete reason', prompts[1])
            directory = engine.directory('GRAFT', 'bytes', 0)
            self.assertTrue((directory / 'validation-attempts/0/evidence.json').exists())
            self.assertTrue((directory / 'validation-attempts/1/evidence.json').exists())
            self.assertIn('2 model calls', json.loads((directory / 'outcome.json').read_text())['reason'])


if __name__ == '__main__':
    unittest.main()
