import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run_round1_recovery import (LIMITS, enforce_cgroup, protected_inventory, reconcile,
                                 state_of, verify_protected_inventory)


class RecoveryStateTests(unittest.TestCase):
    def setUp(self):
        self.protocol={'methods':['GRAFT'],'versions':{'fixture':'0.1.0'}}

    def write(self,root,relative,value):
        path=root/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def test_live_state_precedence_and_no_seed_rewrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            key=('GRAFT','fixture',0)
            self.write(root,'preparation/round-01/GRAFT/fixture/0/task.json',
                       {'method':'GRAFT','crate':'fixture','slot':0,'status':'ready'})
            self.assertEqual(state_of(root,self.protocol,key),'ready_not_started')
            self.write(root,'round-01/GRAFT/fixture/0/started.json',{})
            self.assertEqual(state_of(root,self.protocol,key),'interrupted')
            self.write(root,'round-01/GRAFT/fixture/0/result.json',
                       {'method':'GRAFT','crate':'fixture','slot':0,'status':'measured'})
            self.assertEqual(state_of(root,self.protocol,key),'measured')

    def test_failed_outcome_is_not_requeued(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);key=('GRAFT','fixture',0)
            self.write(root,'preparation/round-01/GRAFT/fixture/0/outcome.json',
                       {'method':'GRAFT','crate':'fixture','slot':0,'status':'validation_rejected'})
            self.assertEqual(state_of(root,self.protocol,key),'validation_rejected')

    def test_fixed_denominator_cannot_shrink(self):
        with tempfile.TemporaryDirectory() as temp:
            states,counts=reconcile(Path(temp),self.protocol)
            self.assertEqual(len(states),20)
            self.assertEqual(counts,{'not_prepared':20})
            broken={'methods':['GRAFT'],'versions':{'fixture':'0.1.0'},'target_slots':19,
                    'fixed_target_slots':20}
            with self.assertRaises(ValueError):
                reconcile(Path(temp),broken)

    def test_execute_requires_exact_memory_and_pid_limits(self):
        good={'memory.high':str(LIMITS['memory_high_bytes']),'memory.max':str(LIMITS['memory_max_bytes']),
              'memory.swap.max':'0','pids.max':str(LIMITS['tasks_max'])}
        with patch('run_round1_recovery.cgroup_limits',return_value=good):
            self.assertEqual(enforce_cgroup(),good)
        bad=dict(good, **{'memory.max':str(100*1024**3)})
        with patch('run_round1_recovery.cgroup_limits',return_value=bad):
            with self.assertRaisesRegex(RuntimeError,'frozen recovery cgroup'):
                enforce_cgroup()

    def test_generation_and_completed_fuzz_are_hash_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            generation=self.write(root,'preparation/round-01/GRAFT/fixture/0/generation.json',{'finished':True})
            result=self.write(root,'round-01/GRAFT/fixture/0/result.json',
                              {'method':'GRAFT','crate':'fixture','slot':0,'status':'measured'})
            before=protected_inventory(root)
            self.assertEqual(len(before['generation_records']),1)
            self.assertEqual(len(before['completed_fuzz_results']),1)
            generation.write_text('{"finished":false}')
            with self.assertRaisesRegex(RuntimeError,'protected prior result'):
                verify_protected_inventory(root,before)


if __name__=='__main__':unittest.main()
