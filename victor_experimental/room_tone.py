"""Reference-derived room tone, continuously advancing through voice and waits.

No random samples, RNG changes, denoising or spectral equalization. The three
intervals and level are the discreet B accepted by the user, not certified pure silence.
"""
import numpy as np
import soundfile as sf
from scipy import signal
from scipy.ndimage import uniform_filter1d


class RoomTone:
    intervals = ((21.49170068027211, 21.73170068027211),
                 (12.422312925170068, 12.662312925170069),
                 (6.984671201814059, 7.224671201814059))
    target_db = -65.00625508816927

    def __init__(self, reference, sample_rate=24000):
        audio, sr = sf.read(reference, dtype="float64")
        self.rate = sample_rate
        self.chunks = []
        self.audit = []
        for start, end in self.intervals:
            part = audio[round(start*sr):round(end*sr)].copy()
            centered = part - part.mean()
            ac = signal.correlate(centered, centered, method="fft", mode="full")[len(centered)-1:]
            periodicity = float(np.max(ac[round(sr/350):round(sr/75)])/ac[0])
            self.audit.append({"start": start, "end": end,
                               "rms_dbfs": float(20*np.log10(np.sqrt(np.mean(part**2)))),
                               "dc": float(part.mean()), "periodicity_75_350hz": periodicity})
            if sr != sample_rate:
                from math import gcd
                divisor = gcd(sr, sample_rate)
                centered = signal.resample_poly(centered, sample_rate//divisor, sr//divisor)
            centered /= np.sqrt(np.mean(centered**2))
            self.chunks.append(centered)
        self.overlap = round(.060*sample_rate)
        # Nonuniform recorded offsets avoid the fixed ~0.88s repeating loop in B.
        # The deterministic schedule neither generates noise nor touches voice RNG.
        self.index = 0
        self.queue = self._chunk()
        self.phase_frames = 0
        self.gain = 10**(self.target_db/20)
        self.last_background_gain = 1.

    def _chunk(self):
        i = self.index
        self.index += 1
        part = self.chunks[i % len(self.chunks)]
        left = round(((i*.6180339887498949) % 1)*.060*self.rate)
        right = round(((i*.4142135623730951) % 1)*.060*self.rate)
        return part[left:len(part)-right].copy()

    def take(self, frames):
        while len(self.queue) < frames+self.overlap:
            following = self._chunk()
            ramp = np.linspace(0, np.pi/2, self.overlap)
            blend = self.queue[-self.overlap:]*np.cos(ramp) + following[:self.overlap]*np.sin(ramp)
            self.queue = np.concatenate([self.queue[:-self.overlap], blend, following[self.overlap:]])
        result = self.queue[:frames].copy()*self.gain
        self.queue = self.queue[frames:]
        self.phase_frames += frames
        return result

    def prepare_voice(self, voice):
        """Add only missing floor. Stronger existing voice/background gets no bed.

        A 60ms smoothing window makes voice/floor transitions gradual. This
        changes only the gain of the reference underlay, never the voice PCM.
        """
        power = uniform_filter1d(np.asarray(voice, dtype=float)**2,
                                 size=round(.020*self.rate), mode="nearest")
        gain = np.sqrt(np.maximum(0., 1-power/(self.gain**2)))
        return uniform_filter1d(gain, size=round(.060*self.rate), mode="nearest")

    def mix(self, voice, gain):
        return voice + self.take(len(voice))*gain


def suppress_isolated_tail_impulse(voice, sr=24000):
    """Conservative version of the accepted local tail correction, no voice trim."""
    voice = np.asarray(voice, dtype=float).copy()
    corrections = []
    lo = max(0, len(voice)-round(.7*sr))
    peak = lo+int(np.argmax(np.abs(voice[lo:])))
    if abs(voice[peak]) > .04 and peak > round(.08*sr) and peak+round(.04*sr) < len(voice):
        before = voice[peak-round(.05*sr):peak-round(.03*sr)]
        after = voice[peak+round(.025*sr):peak+round(.045*sr)]
        if np.sqrt(np.mean(before**2)) < .002 and np.sqrt(np.mean(after**2)) < .002:
            a,b,c,d = [peak+round(v*sr) for v in [-.028,-.013,.012,.032]]
            gain = np.ones(d-a)
            minimum = 10**(-30/20)
            gain[:b-a] = 1-(1-minimum)*.5*(1-np.cos(np.linspace(0,np.pi,b-a)))
            gain[b-a:c-a] = minimum
            gain[c-a:] = minimum+(1-minimum)*.5*(1-np.cos(np.linspace(0,np.pi,d-c)))
            voice[a:d] *= gain
            corrections.append({"peak_seconds": peak/sr, "span_seconds": [a/sr,d/sr], "attenuation_db": -30})
    return voice, corrections
