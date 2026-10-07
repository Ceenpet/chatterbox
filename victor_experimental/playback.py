"""PCM16 in-memory playback through Windows WinMM; no additional packages.

The consumer owns the device. waveOutReset stops queued audio on cancellation.
One complete sentence is submitted per header; no intermediate WAV is needed.
"""
import ctypes as c
import os
import time

class WaveFormat(c.Structure):
    _pack_ = 1
    _fields_ = [("tag", c.c_uint16), ("channels", c.c_uint16),
                ("rate", c.c_uint32), ("bytes_per_second", c.c_uint32),
                ("alignment", c.c_uint16), ("bits", c.c_uint16), ("extra", c.c_uint16)]

class WaveHeader(c.Structure):
    _fields_ = [("data", c.c_void_p), ("length", c.c_uint32),
                ("recorded", c.c_uint32), ("user", c.c_size_t),
                ("flags", c.c_uint32), ("loops", c.c_uint32),
                ("next", c.c_void_p), ("reserved", c.c_size_t)]

class WaveOutPlayer:
    def __init__(self, sample_rate=24000):
        if os.name != "nt":
            raise RuntimeError("This experimental player requires Windows")
        self.rate = sample_rate
        self.mm = c.WinDLL("winmm", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.handle = c.c_void_p()
        self.event = None
        self.kernel.CreateEventW.argtypes = [c.c_void_p, c.c_int, c.c_int, c.c_wchar_p]
        self.kernel.CreateEventW.restype = c.c_void_p
        self.kernel.ResetEvent.argtypes = [c.c_void_p]
        self.kernel.ResetEvent.restype = c.c_int
        self.kernel.WaitForSingleObject.argtypes = [c.c_void_p, c.c_uint32]
        self.kernel.WaitForSingleObject.restype = c.c_uint32
        self.kernel.CloseHandle.argtypes = [c.c_void_p]
        self.kernel.CloseHandle.restype = c.c_int
        self.mm.waveOutOpen.argtypes = [c.POINTER(c.c_void_p), c.c_uint32, c.POINTER(WaveFormat), c.c_size_t, c.c_size_t, c.c_uint32]
        self.mm.waveOutOpen.restype = c.c_uint32
        for name in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
            function = getattr(self.mm, name)
            function.argtypes = [c.c_void_p, c.POINTER(WaveHeader), c.c_uint32]
            function.restype = c.c_uint32
        for name in ("waveOutReset", "waveOutClose"):
            function = getattr(self.mm, name)
            function.argtypes = [c.c_void_p]
            function.restype = c.c_uint32
        self.event = self.kernel.CreateEventW(None, True, False, None)
        if not self.event:
            raise c.WinError(c.get_last_error())
        fmt = WaveFormat(1, 1, sample_rate, sample_rate * 2, 2, 16, 0)
        try:
            self._check(self.mm.waveOutOpen(c.byref(self.handle), 0xFFFFFFFF, c.byref(fmt), self.event, 0, 0x50000), "open")
        except BaseException:
            self.kernel.CloseHandle(self.event)
            self.event = None
            raise

    @staticmethod
    def _check(code, operation):
        if code:
            raise RuntimeError(f"waveOut {operation} failed: MMRESULT={code}")

    def play(self, pcm, cancelled, gate, on_started):
        if len(pcm) % 2 or not pcm:
            raise ValueError("Expected non-empty mono PCM16")
        buffer = c.create_string_buffer(pcm)
        header = WaveHeader(c.cast(buffer, c.c_void_p), len(pcm), 0, 0, 0, 0, None, 0)
        self._check(self.mm.waveOutPrepareHeader(self.handle, c.byref(header), c.sizeof(header)), "prepare")
        start, was_cancelled = None, False
        try:
            # The same gate serializes submit/cancel vs handing old audio to
            # Windows, so a stale block cannot start after a new request wins.
            with gate:
                if cancelled.is_set():
                    return {"cancelled": True, "started": None, "ended": time.perf_counter()}
                self.kernel.ResetEvent(self.event)
                self._check(self.mm.waveOutWrite(self.handle, c.byref(header), c.sizeof(header)), "write")
                start = time.perf_counter()
                on_started(start)
            deadline = start + len(pcm) / (2 * self.rate) + 10.0
            while not (header.flags & 1):  # WHDR_DONE
                if cancelled.is_set():
                    self._check(self.mm.waveOutReset(self.handle), "cancel/reset")
                    was_cancelled = True
                    break
                if time.perf_counter() > deadline:
                    raise RuntimeError("Audio driver did not finish the PCM buffer")
                self.kernel.ResetEvent(self.event)
                result = self.kernel.WaitForSingleObject(self.event, 10)
                if result == 0xFFFFFFFF:
                    raise c.WinError(c.get_last_error())
            return {"cancelled": was_cancelled, "started": start, "ended": time.perf_counter()}
        finally:
            if header.flags & 16:  # WHDR_INQUEUE: retain buffer until returned
                self._check(self.mm.waveOutReset(self.handle), "reset")
            self._check(self.mm.waveOutUnprepareHeader(self.handle, c.byref(header), c.sizeof(header)), "unprepare")

    def close(self):
        if self.handle:
            self._check(self.mm.waveOutReset(self.handle), "close/reset")
            self._check(self.mm.waveOutClose(self.handle), "close")
            self.handle = c.c_void_p()
        if self.event:
            self.kernel.CloseHandle(self.event)
            self.event = None
