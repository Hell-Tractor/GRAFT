import tempfile
import unittest
from pathlib import Path
from deepsurf_repair_support import selected_sources

class SelectedInventory(unittest.TestCase):
    def test_moved_selected_sources_are_retained_and_unselected_are_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);base=root/'deepSURF';base.mkdir()
            (base/'deepsurf_fuzzing_targets.txt').write_text('  * chosen\n')
            for name,where in [('chosen','fuzzing_corpus'),('unselected','no_llm/compilable')]:
                p=base/'fuzz'/where/name/'src';p.mkdir(parents=True);(p/(name+'.rs')).write_text('fn main(){}')
            sources=selected_sources(root)
            self.assertEqual([p.name for p in sources],['chosen.rs'])
    def test_missing_selected_source_is_not_zero_target_success(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'deepSURF').mkdir()
            (root/'deepSURF/deepsurf_fuzzing_targets.txt').write_text('  * missing\n')
            with self.assertRaises(ValueError):selected_sources(root)
    def test_final_location_preferred_over_legacy_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);base=root/'deepSURF';base.mkdir()
            (base/'deepsurf_fuzzing_targets.txt').write_text('  * chosen\n')
            for where in ['fuzzing_corpus','no_llm/compilable']:
                p=base/'fuzz'/where/'chosen/src';p.mkdir(parents=True);(p/'chosen.rs').write_text('fn main(){}')
            self.assertIn('fuzzing_corpus',str(selected_sources(root)[0]))
if __name__=='__main__':unittest.main()
