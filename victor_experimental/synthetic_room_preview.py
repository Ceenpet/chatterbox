"""Isolated statistical noise preview; never imported by the live pipeline."""
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.ndimage import gaussian_filter1d


def main():
    root = Path(__file__).resolve().parents[1]
    ref = root / 'mi_voz.wav'
    profile = json.loads(Path(__file__).with_name('room_tone_profile.json').read_text())
    x, sr = sf.read(ref, dtype='float64')
    assert x.ndim == 1
    spectra, regions = [], []
    for item in profile['selection']:
        part = x[item['start_sample']:item['end_sample']].copy()
        part -= part.mean()
        f, p = signal.welch(part, sr, nperseg=1024, noverlap=512)
        spectra.append(p)
        regions.append({'start_seconds': item['start_seconds'], 'end_seconds': item['end_seconds'],
                        'rms_dbfs': float(20*np.log10(np.sqrt(np.mean(part**2))))})
    # Average statistics only. No phases, waveform fragments or time envelopes survive.
    mean_psd = np.mean(spectra, axis=0)
    grid = np.geomspace(30, sr/2, 512)
    log_psd = np.interp(grid, f, np.log(np.maximum(mean_psd, 1e-20)))
    # Broad ~one-octave smoothing suppresses narrow hum/formant peaks.
    smooth = gaussian_filter1d(log_psd, 24, mode='nearest')
    out_sr = 24000
    design_f = np.linspace(0, out_sr/2, 2049)
    shaped_psd = np.exp(np.interp(design_f, grid, smooth))
    shaped_psd *= np.minimum(design_f/60, 1)**4
    shaped_psd *= 1/(1+(design_f/9500)**12)
    shaped_psd[0] = 0
    gain = np.sqrt(shaped_psd)
    gain /= gain.max()
    taps = signal.firwin2(2049, design_f, gain, fs=out_sr)
    # Independent local RNG: does not touch Chatterbox's seed or global RNG.
    rng = np.random.default_rng(72830419)
    n = 25*out_sr
    white = rng.standard_normal(n+2*len(taps))
    filtered = signal.fftconvolve(white, taps, mode='full')
    y = filtered[2*len(taps):2*len(taps)+n].copy()
    y -= y.mean()
    target = -65.00625508816927
    y *= 10**(target/20)/np.sqrt(np.mean(y*y))
    # Only global preview edges, never repeated fades or cyclic envelopes.
    fade = int(.06*out_sr)
    y[:fade] *= np.sin(np.linspace(0, np.pi/2, fade))**2
    y[-fade:] *= np.cos(np.linspace(0, np.pi/2, fade))**2
    prefix = root / ('victor_room_sintetico_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
    wav = prefix.with_suffix('.wav')
    with wav.open('xb') as handle:
        sf.write(handle, y, out_sr, subtype='PCM_24', format='WAV')
    final, _ = sf.read(wav)
    vf, vp = signal.welch(final[fade:-fade], out_sr, nperseg=8192)
    bands = [(0,80),(80,300),(300,1000),(1000,3000),(3000,8000),(8000,12000)]
    def fractions(freq, power):
        return {f'{lo}-{hi} Hz': round(float(power[(freq>=lo)&(freq<hi)].sum()/power.sum()*100), 2)
                for lo,hi in bands}
    one_sec = [float(20*np.log10(np.sqrt(np.mean(final[i*out_sr:(i+1)*out_sr]**2)))) for i in range(1,24)]
    report = {'wav': str(wav), 'duration_seconds': len(final)/out_sr,
              'reference_sha256': hashlib.sha256(ref.read_bytes()).hexdigest(),
              'regions_statistics_only': regions,
              'reference_mean_psd_band_percent': fractions(f, mean_psd),
              'output_band_percent': fractions(vf, vp),
              'rms_dbfs': float(20*np.log10(np.sqrt(np.mean(final**2)))),
              'one_second_rms_range_dbfs': [min(one_sec), max(one_sec)],
              'dc_offset': float(final.mean()),
              'method': 'Independent Gaussian noise, fixed 2049-tap FIR shaped from broadly smoothed Welch PSD; roll-off below 60 Hz and above 9500 Hz. No source waveform, phase, loop or time envelope copied.',
              'preview_only': True, 'global_edge_fade_ms': 60,
              'note': 'A finite stochastic signal has random fluctuations; no intentional periodic modulation. Statistics cannot certify absence of audible breathing impressions.'}
    prefix.with_suffix('.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
