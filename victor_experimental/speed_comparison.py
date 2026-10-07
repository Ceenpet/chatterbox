"""Offline pitch-preserving A/B/C from existing PCM; no TTS/model imports.

Only comparison outputs are written; pipeline, voice settings and source audio
stay unchanged. WSOLA (waveform similarity overlap-add), mono only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid
import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parents[1]

def wsola(audio, sample_rate, rate):
    """Constant-rate WSOLA, with fixed 50 ms windows, 10 ms output hop,
    and +/- 12 ms waveform-alignment search. No resampling, gain change,
    denoising, edge fades, gap removal or external audio is applied.

    Overlap/windowing is intrinsic to time-stretch, not a separate edit of
    sentence joins. Float64 accumulation avoids unnecessary rounding.
    """
    if not 0.83 <= rate <= 1.0:
        raise ValueError("This experiment supports rates 0.83..1.00 only")
    audio = np.asarray(audio, dtype=np.float64)
    if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
        raise ValueError("Expected finite non-empty mono audio")
    if rate == 1.0:
        return audio.copy()
    window_size = round(0.050 * sample_rate)
    hop = round(0.010 * sample_rate)
    search = round(0.012 * sample_rate)
    overlap = window_size - hop
    output_length = round(len(audio) / rate)
    source = np.pad(audio, (0, window_size + 2 * search))
    output = np.zeros(output_length + window_size)
    weights = np.zeros_like(output)
    # Hamming remains nonzero at the endpoints; normalization preserves the
    # first/last frame without adding a fade or dropping edge samples.
    window = np.hamming(window_size)
    previous_frame = None
    for out_position in range(0, output_length, hop):
        nominal = round(out_position * rate)
        selected = nominal
        if previous_frame is not None:
            target = previous_frame[hop:]
            target_energy = float(np.dot(target, target))
            if target_energy > 1e-12:
                # WSOLA may repeat a locally periodic frame to extend a vowel.
                # A forced minimum advance would prevent period alignment and
                # shift pitch when the required repeat is shorter than a hop.
                low = max(0, nominal - search)
                high = min(len(source) - window_size, nominal + search)
                if low <= high:
                    region = source[low:high + overlap]
                    correlations = signal.correlate(region, target, mode="valid", method="fft")
                    energy = np.concatenate(([0.0], np.cumsum(region * region)))
                    energy = energy[overlap:] - energy[:-overlap]
                    score = correlations / np.sqrt(np.maximum(energy * target_energy, 1e-24))
                    # Prefer the nominal time among effectively equal matches;
                    # avoid arbitrary shifts in sustained periodic vowels.
                    positions = np.arange(low, high + 1)
                    score -= 1e-5 * np.abs(positions - nominal) / max(1, search)
                    selected = low + int(np.argmax(score))
        frame = source[selected:selected + window_size]
        output[out_position:out_position + window_size] += frame * window
        weights[out_position:out_position + window_size] += window
        previous_frame = frame
    return output[:output_length] / weights[:output_length]

def dominant_frequency(audio, sample_rate):
    n = len(audio)
    fft_size = 1 << (n * 4 - 1).bit_length()
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(n), n=fft_size))
    i = int(np.argmax(spectrum[1:])) + 1
    logspec = np.log(np.maximum(spectrum[i - 1:i + 2], 1e-30))
    correction = 0.5 * (logspec[0] - logspec[2]) / (logspec[0] - 2 * logspec[1] + logspec[2])
    return (i + correction) * sample_rate / fft_size

def pitch_track(audio, sample_rate):
    # Independent, confidence-gated autocorrelation measurement. This does not
    # process the comparison audio and is not a perceptual quality score.
    sos = signal.butter(3, [65, 600], fs=sample_rate, btype="bandpass", output="sos")
    filtered = signal.sosfiltfilt(sos, audio)
    frame_size, step = round(0.080 * sample_rate), round(0.020 * sample_rate)
    lag_min, lag_max = round(sample_rate / 350), round(sample_rate / 75)
    result = []
    for start in range(0, len(audio) - frame_size, step):
        frame = filtered[start:start + frame_size].copy()
        if np.sqrt(np.mean(frame * frame)) < 0.004:
            continue
        frame -= frame.mean()
        corr = signal.correlate(frame, frame, mode="full", method="fft")[frame_size - 1:]
        energies = np.concatenate(([0.0], np.cumsum(frame * frame)))
        lags = np.arange(lag_min, lag_max + 1)
        norm = np.sqrt(np.maximum(energies[frame_size - lags] * (energies[frame_size] - energies[lags]), 1e-24))
        values = corr[lags] / norm
        peaks, _ = signal.find_peaks(values, height=0.75)
        if not len(peaks):
            continue
        best = peaks[int(np.argmax(values[peaks]))]
        # Earliest strong periodic peak reduces octave-down ambiguity.
        eligible = peaks[values[peaks] >= max(0.80, values[best] - 0.035)]
        if len(eligible):
            best = eligible[0]
        lag = lags[best]
        if 0 < best < len(values) - 1:
            a, b, d = values[best - 1:best + 2]
            denominator = a - 2 * b + d
            if abs(denominator) > 1e-12:
                lag += 0.5 * (a - d) / denominator
        result.append(((start + frame_size / 2) / sample_rate, sample_rate / lag, float(values[best])))
    return result

def aligned_pitch_check(original, stretched, sample_rate, rate):
    a = pitch_track(original, sample_rate)
    b = pitch_track(stretched, sample_rate)
    pairs = []
    for tb, fb, _ in b:
        ta = tb * rate
        if a:
            nearest = min(a, key=lambda x: abs(x[0] - ta))
            if abs(nearest[0] - ta) <= 0.025:
                cents = 1200 * np.log2(fb / nearest[1])
                # Keep octave errors visible separately from central tendency.
                pairs.append(float(cents))
    clean = [x for x in pairs if abs(x) < 100]
    return {"voiced_pairs": len(pairs), "pairs_within_one_semitone": len(clean),
            "median_signed_cents": float(np.median(clean)) if clean else None,
            "median_absolute_cents": float(np.median(np.abs(clean))) if clean else None,
            "p90_absolute_cents": float(np.percentile(np.abs(clean), 90)) if clean else None,
            "pairs_outside_one_semitone": len(pairs) - len(clean),
            "note": "Autocorrelation estimate with temporal matching; outliers can reflect intonation/transient alignment or pitch estimator error. Does not certify absence of perceptual artifacts."}

def benchmark_segment_costs(audio, sample_rate, rate, metrics):
    """Existing PCM only, no TTS: estimate CPU cost if stretching were placed
    in the synthesis producer. This is a timing projection, not integration.
    """
    records = metrics["response"]["records"]
    position, previous_start, previous_duration = 0, None, None
    costs = []
    for record in records:
        started = record["playback_start_seconds"]
        gap = 0 if previous_start is None else max(0, started - previous_start - previous_duration)
        position += round(gap * sample_rate)
        count = round(record["duration_seconds"] * sample_rate)
        segment = audio[position:position + count]
        assert len(segment) == count
        trials = []
        for _ in range(3):
            before = time.perf_counter()
            wsola(segment, sample_rate, rate)
            trials.append(time.perf_counter() - before)
        costs.append(float(np.median(trials)))
        position += count
        previous_start, previous_duration = started, count / sample_rate
    assert position == len(audio), "Export segment offsets do not match metadata"
    return costs

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True,
                        help="Explicit legacy progressive report for this optional offline comparison")
    args = parser.parse_args()
    METRICS = args.metrics.resolve()
    source = args.source.resolve()
    original_bytes = source.read_bytes()
    original_hash = hashlib.sha256(original_bytes).hexdigest()
    audio, sample_rate = sf.read(source, dtype="float64")
    if sample_rate != 24000 or audio.ndim != 1:
        raise RuntimeError("Expected existing mono 24000 Hz reference output")
    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    speech_duration = metrics["total_audio_seconds"]
    prefix_duration = sum(r["duration_seconds"] for r in metrics["response"]["records"][:2])
    gap = metrics["response"]["records"][2]["gap_before_seconds"]
    run = "victor_velocidad_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    report = {"source": str(source), "source_sha256": original_hash,
              "method": "WSOLA waveform similarity overlap-add, 50 ms window / 10 ms hop / 12 ms search, no resampling",
              "A": {"rate": 1.0, "wav": str(source), "byte_identical_original": True},
              "scope": "Whole existing WAV including existing pauses. No TTS, no pipeline changes, no silence edits, no extra fades, no background sound.",
              "generation_metrics_source": str(METRICS),
              "existing_gap_seconds": gap, "prefix_speech_seconds": prefix_duration,
              "quality_limitation": "Technical checks are not a listening test; absence of perceptible artifacts requires human review.",
              "variants": []}
    for label, rate in [("B", 0.95), ("C", 0.93)]:
        # Pitch calibration of the processor with known harmonically rich tones.
        tone_checks = []
        for frequency in [110.0, 173.0, 237.0]:
            t = np.arange(sample_rate * 2) / sample_rate
            tone = 0.3 * np.sin(2 * np.pi * frequency * t) + 0.06 * np.sin(4 * np.pi * frequency * t)
            tone_out = wsola(tone, sample_rate, rate)
            measured = dominant_frequency(tone_out[sample_rate // 10:-sample_rate // 10], sample_rate)
            cents = float(1200 * np.log2(measured / frequency))
            assert abs(cents) < 10, f"Pitch calibration failed: {frequency} Hz / {cents} cents"
            tone_checks.append({"input_hz": frequency, "output_hz": measured, "shift_cents": cents})
        started = time.perf_counter()
        slowed = wsola(audio, sample_rate, rate)
        processing_seconds = time.perf_counter() - started
        assert len(slowed) == round(len(audio) / rate)
        assert np.isfinite(slowed).all()
        assert np.max(np.abs(slowed)) < 1, "Would clip; no automatic normalization allowed"
        pitch = aligned_pitch_check(audio, slowed, sample_rate, rate)
        assert pitch["voiced_pairs"] > 20
        assert abs(pitch["median_signed_cents"]) < 15, "Potential systematic pitch shift"
        output = ROOT / (run + f"_{label}_{rate:.2f}.wav")
        with output.open("xb") as handle:
            sf.write(handle, slowed, sample_rate, format="WAV", subtype="PCM_16")
        prefix_margin = prefix_duration * (1 / rate - 1)
        record = {"label": label, "rate": rate, "wav": str(output),
                  "duration_seconds": len(slowed) / sample_rate,
                  "full_wav_added_seconds": len(slowed) / sample_rate - len(audio) / sample_rate,
                  "speech_added_seconds": speech_duration * (1 / rate - 1),
                  "prefix_added_seconds": prefix_margin,
                  "predicted_gap_remaining_seconds": max(0, gap - prefix_margin),
                  "prediction_assumption": "Same synthesis times, no added processing cost or scheduling changes. Estimated concurrent playback, not the pause baked into the comparison WAV.",
                  "effective_weighted_generation_over_audio": metrics["weighted_generation_over_audio"] * rate,
                  "processing_seconds_whole_wav": processing_seconds,
                  "peak": float(np.max(np.abs(slowed))),
                  "clipped_samples": int(np.count_nonzero(np.abs(slowed) >= 1)),
                  "rms_db_change": float(20 * np.log10(np.sqrt(np.mean(slowed * slowed)) / np.sqrt(np.mean(audio * audio)))),
                  "max_adjacent_sample_difference": float(np.max(np.abs(np.diff(slowed)))),
                  "original_max_adjacent_sample_difference": float(np.max(np.abs(np.diff(audio)))),
                  "pitch_calibration": tone_checks, "voice_pitch_estimate": pitch,
                  "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
        costs = benchmark_segment_costs(audio, sample_rate, rate, metrics)
        record["median_processing_seconds_per_segment"] = costs
        # First-segment processing delays both playback start and all following
        # generation equally; the second/third costs erode the accumulated lead.
        record["predicted_gap_if_stretch_in_serial_producer_seconds"] = max(0, gap - prefix_margin + sum(costs[1:]))
        record["serial_cost_prediction_note"] = "Assumes processing is serialized after each segment, original synthesis timings unchanged. Not a measured concurrent pipeline run; parallel CPU/GPU scheduling could differ."
        report["variants"].append(record)
        print("VARIANT", json.dumps(record), flush=True)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    report_path = ROOT / (run + ".json")
    with report_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("REPORT", report_path, flush=True)

if __name__ == "__main__":
    main()
