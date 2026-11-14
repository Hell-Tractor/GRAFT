import copy
import unittest

from check_public_surface import doctest_analysis_view


class DoctestAnalysisViewTests(unittest.TestCase):
    def test_only_known_doctest_attribute_is_closed_in_a_copy(self):
        raw = {"parser": {"krate": {"index": {"1": {"name": "sample", "attrs": [
            {"other": "#[doc(test(attr(deny(warnings, rust_2018_idioms))))"},
            {"other": "#[doc(hidden)]"}, {"other": "#[cfg(feature = broken)"}]}}}}}
        original = copy.deepcopy(raw)
        view, edits = doctest_analysis_view(raw)
        self.assertEqual(raw, original)
        self.assertEqual(len(edits), 1)
        attrs = view["parser"]["krate"]["index"]["1"]["attrs"]
        self.assertTrue(attrs[0]["other"].endswith("]"))
        self.assertEqual(attrs[1:], original["parser"]["krate"]["index"]["1"]["attrs"][1:])

    def test_correct_or_unrecognized_attributes_stay_unchanged(self):
        raw = {"index": {"1": {"attrs": [{"other": "#[doc(test(attr(allow(dead_code))))]"},
                                         {"other": "#[doc(hidden)"}]}}}
        view, edits = doctest_analysis_view(raw)
        self.assertEqual(view, raw)
        self.assertEqual(edits, [])


if __name__ == "__main__":
    unittest.main()
