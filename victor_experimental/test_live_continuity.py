"""Actual WinMM tests: starvation fill, exact capture, cancellation while waiting."""
from pathlib import Path
import threading
import time
import unittest
import numpy as np
from .live_continuity import LiveContinuity
from .continuous_audio import ContinuousDevice
from .synthetic_room_tone import RoomTone

ROOT = Path(__file__).resolve().parents[1]
TEXT = "Esta es una primera frase suficientemente larga. Esta es otra frase suficientemente larga."


class Engine:
    def __init__(self, delays=(.03,.40), duration=.10):
        self.delays = delays
        self.duration = duration
        self.index = 0
        self.second_started = threading.Event()

    def synthesize(self, text):
        index = self.index
        self.index += 1
        if index:
            self.second_started.set()
        time.sleep(self.delays[min(index,len(self.delays)-1)])
        return np.zeros(round(self.duration*24000)), {"synthesis_seconds": self.delays[min(index,len(self.delays)-1)]}


class Tests(unittest.TestCase):
    def test_failed_device_initialization_releases_response_lock(self):
        def unavailable(*args,**kwargs):
            raise RuntimeError('device unavailable')
        stream=LiveContinuity(Engine(),ROOT/'mi_voz.wav',device_factory=unavailable)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError,'device unavailable'):
                stream.run(TEXT)

    def test_adaptive_reserve_counts_voice_not_room_wait(self):
        text=TEXT+' Esta última frase termina la comprobación del margen.'
        stream=LiveContinuity(Engine(delays=(.01,.35,.01)),ROOT/'mi_voz.wav',strategy='rapido')
        result=stream.run(text)
        self.assertEqual(len(result['records']),3)
        snapshot=result['records'][2]['segmentation']['snapshot']
        self.assertGreater(snapshot['room_only_total_seconds'],.1)
        self.assertGreater(snapshot['voice_ahead_seconds'],0)
        self.assertLessEqual(snapshot['voice_ahead_seconds'],.25)

    def test_room_only_is_continuous_and_no_voice_rng_changes(self):
        before = np.random.get_state()
        room = RoomTone(ROOT/'mi_voz.wav')
        samples = room.take(24000*3)
        after = np.random.get_state()
        self.assertTrue(np.array_equal(before[1],after[1]))
        self.assertTrue(np.isfinite(samples).all())
        measured_db = 20*np.log10(np.sqrt(np.mean(samples**2)))
        self.assertAlmostEqual(measured_db, room.target_db, delta=.5)
        self.assertLess(np.max(np.abs(np.diff(samples))),.02)
        self.assertTrue(np.all(room.prepare_voice(np.ones(24000)*.1)==0))

    def test_synthetic_filter_keeps_state_across_pcm_blocks(self):
        whole = RoomTone(ROOT/'mi_voz.wav').take(24000)
        stream = RoomTone(ROOT/'mi_voz.wav')
        blocked = np.concatenate([stream.take(960) for _ in range(25)])
        np.testing.assert_allclose(whole, blocked, rtol=0, atol=1e-15)
        self.assertFalse(stream.audit[0]['copied_audio_samples'])
        self.assertFalse(stream.audit[0]['loop'])

    def test_real_device_wait_fill_and_exact_capture(self):
        stream = LiveContinuity(Engine(), ROOT/'mi_voz.wav', capture=True,strategy='rapido')
        result = stream.run(TEXT)
        self.assertFalse(result['cancelled'])
        self.assertGreater(result['room_only_wait_seconds'],.1)
        self.assertEqual(result['driver_position_frames'],result['output_frames'])
        self.assertEqual(sum(len(p) for p in result['pcm_blocks'])//2,result['output_frames'])
        self.assertEqual(result['underruns_observed'],[])

    def test_cancel_during_room_tone_discards_future_voice(self):
        engine = Engine(delays=(.01,.7),duration=.05)
        stream = LiveContinuity(engine, ROOT/'mi_voz.wav',capture=False,strategy='rapido')
        result = []
        worker = threading.Thread(target=lambda: result.append(stream.run(TEXT)))
        worker.start()
        self.assertTrue(engine.second_started.wait(3))
        time.sleep(.28)
        stream.cancel()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result[0]['cancelled'])
        self.assertLess(result[0]['cancel_stop_latency_seconds'],.05)
        self.assertEqual(len(result[0]['records']),1)


if __name__ == '__main__':
    unittest.main()
