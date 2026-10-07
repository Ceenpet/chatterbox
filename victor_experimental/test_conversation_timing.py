import unittest
from .conversation_timing import startup_hold

class Tests(unittest.TestCase):
    def record(self, ratio=1., final=False, duration=6, next_chars=70):
        return dict(synthesis_seconds=duration*ratio, speech_segment_seconds=duration,
                    played_voice_seconds=duration+.35, text='x'*69,
                    segmentation=dict(is_final=final, next_natural_unit_chars=next_chars))

    def test_bounded_reserve_and_no_delay_for_fast_or_complete_answer(self):
        self.assertEqual(startup_hold(self.record(), 'tranquilo'), .6)
        self.assertGreater(startup_hold(self.record(1.2), 'tranquilo'), .6)
        self.assertEqual(startup_hold(self.record(10), 'tranquilo'), 3)
        self.assertEqual(startup_hold(self.record(final=True), 'tranquilo'), 0)
        self.assertEqual(startup_hold(self.record(), 'rapido'), 0)

    def test_short_intro_long_unit_moves_wait_before_speech(self):
        r=self.record(duration=2.8, next_chars=173)
        r['text']='x'*33
        self.assertGreater(startup_hold(r, 'tranquilo'), 3)
        self.assertLessEqual(startup_hold(r, 'tranquilo'), 12)
