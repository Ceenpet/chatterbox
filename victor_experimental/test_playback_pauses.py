import unittest
from .live_continuity import boundary_pause, PLAYBACK_SPEED

class Tests(unittest.TestCase):
    def test_semantic_boundaries_only(self):
        self.assertEqual(PLAYBACK_SPEED, 1.)
        for boundary, expected in [('sentence', .35), ('clause', .30), ('discourse_comma', .30), ('long_clause_word_boundary', 0), ('unbroken_token_exceeds_bound', 0)]:
            for index in (0, 1, 8):
                self.assertEqual(boundary_pause(dict(boundary=boundary, index=index, is_final=False)), expected)
            self.assertEqual(boundary_pause(dict(boundary=boundary, is_final=True)), 0)
