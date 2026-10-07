"""Queued WinMM PCM stream. No model imports; one device per response.

Four 40ms buffers are queued ahead. Every submitted byte is captured on the
same path as waveOutWrite. Device sample position and buffer flags expose
queue starvation; this is not an acoustic loopback or a hardware xrun counter.
"""
import ctypes as c
import time
from .playback import WaveOutPlayer, WaveHeader


class TimeValue(c.Union):
    _fields_ = [("samples", c.c_uint32), ("milliseconds", c.c_uint32),
                ("padding", c.c_byte * 8)]


class MultimediaTime(c.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", c.c_uint32), ("value", TimeValue)]


class ContinuousDevice(WaveOutPlayer):
    def __init__(self, sample_rate=24000, depth=4, capture=True):
        super().__init__(sample_rate)
        self.depth = depth
        self.pending = []
        self.capture = [] if capture else None
        self.submitted_frames = 0
        self.starvations = []
        self.initial_pause = True
        self.running = False
        self.first_submit = None
        self.first_restart = None
        self.stop_ack = None
        self.position_before_cancel = None
        self.mm.waveOutGetPosition.argtypes = [c.c_void_p, c.POINTER(MultimediaTime), c.c_uint32]
        self.mm.waveOutGetPosition.restype = c.c_uint32
        for name in ("waveOutPause", "waveOutRestart"):
            getattr(self.mm, name).argtypes = [c.c_void_p]
            getattr(self.mm, name).restype = c.c_uint32
        self._check(self.mm.waveOutPause(self.handle), "initial pause")

    def position(self):
        position = MultimediaTime()
        position.type = 2  # TIME_SAMPLES
        self._check(self.mm.waveOutGetPosition(self.handle, c.byref(position), c.sizeof(position)), "position")
        if position.type == 2:
            return int(position.samples)
        if position.type == 1:
            return round(position.milliseconds * self.rate / 1000)
        raise RuntimeError(f"Unsupported waveOut position format: {position.type}")

    def reap(self):
        while self.pending and self.pending[0][1].flags & 1:
            buffer, header = self.pending.pop(0)
            self._check(self.mm.waveOutUnprepareHeader(self.handle, c.byref(header), c.sizeof(header)), "unprepare")

    def write(self, pcm):
        if not pcm or len(pcm) % 2:
            raise ValueError("Expected nonempty PCM16")
        self.reap()
        if len(self.pending) >= self.depth:
            raise RuntimeError("Device queue capacity exceeded")
        if self.running and not self.pending:
            self.starvations.append({"time": time.perf_counter(), "position_frames": self.position(),
                                     "submitted_frames": self.submitted_frames})
        buffer = c.create_string_buffer(pcm)
        header = WaveHeader(c.cast(buffer, c.c_void_p), len(pcm), 0, 0, 0, 0, None, 0)
        self._check(self.mm.waveOutPrepareHeader(self.handle, c.byref(header), c.sizeof(header)), "prepare")
        try:
            self._check(self.mm.waveOutWrite(self.handle, c.byref(header), c.sizeof(header)), "write")
        except BaseException:
            self.mm.waveOutUnprepareHeader(self.handle, c.byref(header), c.sizeof(header))
            raise
        self.pending.append((buffer, header))  # retain memory until driver returns it
        if self.capture is not None:
            self.capture.append(pcm)
        self.submitted_frames += len(pcm)//2
        if self.first_submit is None:
            self.first_submit = time.perf_counter()

    def restart(self):
        if self.initial_pause:
            self._check(self.mm.waveOutRestart(self.handle), "restart")
            self.first_restart = time.perf_counter()
            self.initial_pause = False
            self.running = True

    def cancel(self):
        self.position_before_cancel = self.position()
        self._check(self.mm.waveOutReset(self.handle), "cancel/reset")
        self.running = False
        self.stop_ack = time.perf_counter()
        self.reap()
        if self.pending:
            raise RuntimeError("Reset did not return queued buffers")

    def close(self):
        if self.pending:
            self.cancel()
        super().close()
