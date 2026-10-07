"""Real producer -> bounded queue -> continuous voice/room-tone device output.

Public API: LiveContinuity(engine, reference).run(text); .cancel() from another
thread stops both voice and underlay. Generation already in flight finishes and
is discarded. Only one response may run at a time on this engine.
"""
import queue
import threading
import time
import numpy as np

from .continuous_audio import ContinuousDevice
from .room_tone import suppress_isolated_tail_impulse
from .synthetic_room_tone import RoomTone
from .adaptive_segments import AdaptiveSegmenter
from .speed_comparison import wsola


class LiveContinuity:
    def __init__(self, engine, reference, device_factory=ContinuousDevice, capture=False, strategy="tranquilo"):
        self.engine = engine
        self.reference = reference
        self.device_factory = device_factory
        self.capture = capture
        self.strategy = strategy
        self.cancelled = threading.Event()
        self.cancel_requested = None
        self.lock = threading.Lock()

    def cancel(self):
        self.cancel_requested = time.perf_counter()
        self.cancelled.set()

    def run(self, text, strategy=None):
        planner = AdaptiveSegmenter(text, strategy or self.strategy)
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("One response at a time; cancel and await prior run before replacing")
        self.cancelled.clear()
        self.cancel_requested = None
        sr, block_size = 24000, 960  # 40ms; 6 queued blocks = 240ms scheduling reserve
        segments = []
        if planner.finished:
            self.lock.release()
            raise ValueError("Empty response")
        try:
            room = RoomTone(self.reference, sr)
        except BaseException:
            self.lock.release()
            raise
        audio_queue = queue.Queue(maxsize=2)
        producer_done = threading.Event()
        records, errors = [], []
        metrics_lock = threading.Lock()
        reserve = {"ready_voice_frames":0, "played_voice_frames":0, "room_only_total_seconds":0.}
        submitted = time.perf_counter()
        device = None
        worker = None

        def put(item):
            while not self.cancelled.is_set():
                try:
                    audio_queue.put(item, timeout=.02)
                    return True
                except queue.Full:
                    pass
            return False

        def produce():
            try:
                while not planner.finished:
                    if self.cancelled.is_set():
                        break
                    with metrics_lock:
                        snapshot = {"voice_ahead_seconds":max(0,reserve["ready_voice_frames"]-reserve["played_voice_frames"])/sr,
                                    "room_only_total_seconds":reserve["room_only_total_seconds"]}
                    sentence, decision = planner.next(snapshot)
                    index = decision["index"]
                    segments.append(sentence)
                    start = time.perf_counter()
                    voice, metrics = self.engine.synthesize(sentence)
                    synthesis_done = time.perf_counter()
                    if self.cancelled.is_set():
                        break
                    voice = wsola(voice, sr, .83)
                    voice, corrections = suppress_isolated_tail_impulse(voice, sr)
                    gain = room.prepare_voice(voice)
                    ready = time.perf_counter()
                    record = dict(metrics, index=index, text=sentence,
                                  synthesis_start_seconds=start-submitted,
                                  synthesis_done_seconds=synthesis_done-submitted,
                                  ready_seconds=ready-submitted,
                                  postprocessing_seconds=ready-synthesis_done,
                                  played_voice_seconds=len(voice)/sr,
                                  segmentation=decision,
                                  generation_over_stretched_audio=metrics["synthesis_seconds"]/(len(voice)/sr),
                                  tail_impulse_corrections=corrections)
                    with metrics_lock:
                        records.append(record)
                        reserve["ready_voice_frames"] += len(voice)
                    planner.observe(sentence, metrics["synthesis_seconds"],len(voice)/sr,ready-synthesis_done)
                    if not put((record, voice, gain)):
                        break
            except BaseException as exc:
                errors.append(repr(exc))
                self.cancelled.set()
            finally:
                producer_done.set()

        cursor = None
        position = 0
        output_frames = 0
        waits = []
        wait_start = None
        background_only_frames = 0
        background_nonzero_gain_frames = 0
        first_ready = None
        first_voice_start = None
        last_gain = 0.
        segment_entry_gain = 0.
        wait_entry_gain = 1.
        last_metric_sample = 0.
        try:
            device = self.device_factory(sr, depth=6, capture=self.capture)
            worker = threading.Thread(target=produce, name="victor-live-synthesis")
            worker.start()
            # Device is already open; playback begins when first voice is ready.
            # No misleading initial room-only "first audio" latency metric.
            while cursor is None and not self.cancelled.is_set():
                try:
                    cursor = audio_queue.get(timeout=.02)
                    first_ready = time.perf_counter()-submitted
                except queue.Empty:
                    if producer_done.is_set():
                        break
            completed = False
            deadline = time.perf_counter()+300
            while cursor is not None or not completed:
                if self.cancelled.is_set():
                    device.cancel()
                    break
                if cursor is None and first_ready is None:
                    break
                device.reap()
                # Sample only on the device-owning thread. Ready audio includes
                # current, queued and producer-held PCM; room-only buffers do not
                # count as useful voice reserve. Never query the driver in TTS.
                if time.perf_counter()-last_metric_sample >= .04:
                    driver_frames = device.position()
                    with metrics_lock:
                        reserve["played_voice_frames"] = sum(
                            max(0,min(driver_frames-round(r.get("pcm_start_seconds",0)*sr),r.get("pcm_sent_frames",0)))
                            for r in records if "pcm_start_seconds" in r)
                    last_metric_sample = time.perf_counter()
                if len(device.pending) >= device.depth:
                    device.restart()
                    self.cancelled.wait(.003)
                    if time.perf_counter() > deadline:
                        raise RuntimeError("Response/device timeout")
                    continue
                if cursor is not None and position == len(cursor[1]):
                    cursor[0]["pcm_end_seconds"] = output_frames/sr
                    cursor = None
                    position = 0
                if cursor is None:
                    try:
                        cursor = audio_queue.get_nowait()
                    except queue.Empty:
                        if producer_done.is_set():
                            completed = True
                            break
                if cursor is not None:
                    record, voice, gain = cursor
                    if position == 0:
                        segment_entry_gain = last_gain
                        record["pcm_start_seconds"] = output_frames/sr
                        record["device_submit_wall_seconds"] = time.perf_counter()-submitted
                        if first_voice_start is None:
                            first_voice_start = record["device_submit_wall_seconds"]
                        if wait_start is not None:
                            waits.append({"pcm_start_seconds": wait_start/sr,
                                          "pcm_end_seconds": output_frames/sr,
                                          "room_only_seconds": (output_frames-wait_start)/sr})
                            wait_start = None
                    count = min(block_size, len(voice)-position)
                    samples = voice[position:position+count]
                    gains = gain[position:position+count].copy()
                    # Match the incoming bed gain to the preceding block, then
                    # approach the precomputed complementary floor smoothly.
                    if position < round(.06*sr):
                        remaining = round(.06*sr)-position
                        n = min(count, remaining)
                        factor = .5*(1-np.cos(np.arange(position,position+n)/(.06*sr-1)*np.pi))
                        gains[:n] = segment_entry_gain*(1-factor)+gains[:n]*factor
                    if record["segmentation"]["is_final"]:
                        remaining = len(voice)-position
                        if remaining <= round(.05*sr)+count:
                            indices = np.arange(position,position+count)
                            factor = np.clip((len(voice)-1-indices)/(.05*sr),0,1)
                            gains *= .5*(1-np.cos(np.pi*factor))
                    mixed = room.mix(samples, gains)
                    position += count
                    last_gain = float(gains[-1])
                    background_nonzero_gain_frames += int(np.count_nonzero(gains > 1e-6))
                else:
                    if wait_start is None:
                        wait_start = output_frames
                        wait_entry_gain = last_gain
                    count = block_size
                    elapsed = output_frames-wait_start
                    factor = np.clip(np.arange(elapsed,elapsed+count)/(.06*sr-1),0,1)
                    factor = .5*(1-np.cos(np.pi*factor))
                    gains = wait_entry_gain*(1-factor)+factor
                    mixed = room.take(count)*gains
                    last_gain = float(gains[-1])
                    background_only_frames += count
                    with metrics_lock:
                        reserve["room_only_total_seconds"] = background_only_frames/sr
                    background_nonzero_gain_frames += count
                # One global fade for the underlay is implicit in first segment
                # gain ramp; voice itself is never faded here or normalized.
                if np.max(np.abs(mixed)) >= 1:
                    raise RuntimeError("PCM would clip; no automatic gain normalization")
                pcm = np.rint(mixed*32768).astype('<i2').tobytes()
                if self.cancelled.is_set():
                    device.cancel()
                    break
                device.write(pcm)
                if cursor is not None:
                    with metrics_lock:
                        record["pcm_sent_frames"] = record.get("pcm_sent_frames",0)+count
                output_frames += count
            if not self.cancelled.is_set():
                device.restart()
            while device.pending and not self.cancelled.is_set():
                device.reap()
                self.cancelled.wait(.003)
                if time.perf_counter() > deadline:
                    raise RuntimeError("Device drain timeout")
            if self.cancelled.is_set() and device.stop_ack is None:
                device.cancel()
            final_position = device.position()
            pcm_blocks = list(device.capture) if device.capture is not None else None
            result = {"segments": segments, "records": records, "cancelled": self.cancelled.is_set(),
                      "strategy":planner.strategy.name,
                      "errors": errors, "first_pcm_ready_seconds": first_ready,
                      "first_voice_submit_seconds": first_voice_start,
                      "first_device_restart_seconds": None if device.first_restart is None else device.first_restart-submitted,
                      "first_audio_definition": "First voice waveOutRestart; excludes model startup, not loopback acoustic onset",
                      "output_frames": output_frames, "duration_seconds": output_frames/sr,
                      "driver_position_frames": final_position,
                      "driver_position_before_cancel_frames": device.position_before_cancel,
                      "room_only_wait_seconds": background_only_frames/sr,
                      "room_nonzero_gain_seconds": background_nonzero_gain_frames/sr,
                      "generation_waits_in_pcm": waits,
                      "underruns_observed": device.starvations,
                      "underrun_definition": "No outstanding WinMM buffer at replenishment; not a hardware/loopback xrun counter",
                      "room_tone_audit": room.audit, "room_tone_target_dbfs": room.target_db,
                      "room_tone_schedule": room.schedule,
                      "room_gain_policy": "Complement missing floor from 20ms voice energy, smooth 60ms, retain phase through voice and waits",
                      "cancel_stop_latency_seconds": None if self.cancel_requested is None or device.stop_ack is None else device.stop_ack-self.cancel_requested,
                      "pcm_blocks": pcm_blocks}
            if not result["cancelled"]:
                assert final_position == output_frames, (final_position,output_frames)
            return result
        finally:
            self.cancelled.set()
            try:
                if device:
                    device.close()
            finally:
                try:
                    if worker:
                        worker.join(60)
                        if worker.is_alive():
                            raise RuntimeError("In-flight generation did not finish after cancellation")
                    while not audio_queue.empty():
                        audio_queue.get_nowait()
                finally:
                    self.lock.release()
