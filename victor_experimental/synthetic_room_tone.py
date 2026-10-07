"""Continuous independent noise with the approved preview's fixed spectral filter."""
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.ndimage import gaussian_filter1d

from .room_tone import RoomTone as RecordedRoomTone


class RoomTone(RecordedRoomTone):
    schedule = 'Continuous Gaussian noise through fixed FIR; local RNG, no loops or copied source samples'

    def __init__(self, reference, sample_rate=24000):
        self.rate = sample_rate
        profile_path = Path(__file__).with_name('room_tone_profile.json')
        profile = json.loads(profile_path.read_text())
        audio, sr = sf.read(reference, dtype='float64')
        spectra = []
        for item in profile['selection']:
            part = audio[item['start_sample']:item['end_sample']].copy()
            part -= part.mean()
            frequencies, psd = signal.welch(part, sr, nperseg=1024, noverlap=512)
            spectra.append(psd)
        grid = np.geomspace(30, sr/2, 512)
        smooth = gaussian_filter1d(np.interp(grid, frequencies,
                    np.log(np.maximum(np.mean(spectra, axis=0), 1e-20))), 24, mode='nearest')
        design_f = np.linspace(0, sample_rate/2, 2049)
        shaped_psd = np.exp(np.interp(design_f, grid, smooth))
        shaped_psd *= np.minimum(design_f/60, 1)**4
        shaped_psd *= 1/(1+(design_f/9500)**12)
        shaped_psd[0] = 0
        gain = np.sqrt(shaped_psd)
        gain /= gain.max()
        self.taps = signal.firwin2(2049, design_f, gain, fs=sample_rate)
        self.gain = 10**(self.target_db/20)
        self.taps *= self.gain / np.sqrt(np.sum(self.taps**2))
        self.rng = np.random.default_rng(72830419)
        self.state = np.zeros(len(self.taps)-1)
        # Prime the filter once. State is retained across every PCM request.
        _, self.state = signal.lfilter(self.taps, [1.], self.rng.standard_normal(4098), zi=self.state)
        self.phase_frames = 0
        self.last_background_gain = 1.
        self.audit = [{'source': 'synthetic', 'statistics_only': True,
                       'reference_intervals': profile['selection'],
                       'fir_taps': len(self.taps), 'target_dbfs': self.target_db,
                       'copied_audio_samples': False, 'loop': False}]

    def take(self, frames):
        result, self.state = signal.lfilter(self.taps, [1.],
                            self.rng.standard_normal(frames), zi=self.state)
        self.phase_frames += frames
        return result
