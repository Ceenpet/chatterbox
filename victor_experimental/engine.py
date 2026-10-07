"""Persistent CUDA model with the fixed, selected voice and RNG semantics."""
from dataclasses import dataclass
from pathlib import Path
import random
import threading
import time
import numpy as np
import torch
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

@dataclass(frozen=True)
class GoldenVoice:
    seed: int = 1928081889104100
    temperature: float = 0.60
    exaggeration: float = 0.50
    cfg_weight: float = 0.50
    language: str = "es"
    t3: str = "t3_es_es.safetensors"
    s3gen: str = "s3gen.pt"
    reference: str = "mi_voz.wav"

VOICE = GoldenVoice()

def seed_golden():
    random.seed(VOICE.seed)
    np.random.seed(VOICE.seed % (2**32))
    torch.manual_seed(VOICE.seed)
    torch.cuda.manual_seed_all(VOICE.seed)

def rng_snapshot():
    return {"python": random.getstate(), "numpy": np.random.get_state(),
            "torch_cpu": torch.get_rng_state().clone(),
            "torch_cuda": [x.clone() for x in torch.cuda.get_rng_state_all()]}

def restore_rng(state):
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch_cpu"])
    torch.cuda.set_rng_state_all(state["torch_cuda"])

def rng_changes(before, after):
    return {"python": before["python"] != after["python"],
            "numpy": not (before["numpy"][0] == after["numpy"][0] and
                           np.array_equal(before["numpy"][1], after["numpy"][1]) and
                           before["numpy"][2:] == after["numpy"][2:]),
            "torch_cpu": not torch.equal(before["torch_cpu"], after["torch_cpu"]),
            "torch_cuda": any(not torch.equal(a, b) for a, b in zip(before["torch_cuda"], after["torch_cuda"]))}

def cuda_memory():
    free, total = torch.cuda.mem_get_info()
    return {"allocated_gib": torch.cuda.memory_allocated() / 1024**3,
            "reserved_gib": torch.cuda.memory_reserved() / 1024**3,
            "device_free_gib": free / 1024**3, "device_total_gib": total / 1024**3}

class VoiceEngine:
    def __init__(self, root):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is mandatory. No CPU fallback.")
        self.root = Path(root).resolve()
        self.lock = threading.Lock()
        started = time.perf_counter()
        # Uses the unchanged loader, which strictly loads the selected s3gen.pt.
        self.model = ChatterboxMultilingualTTS.from_local(
            self.root / "checkpoints_es_es", device="cuda", t3_model=VOICE.t3)
        for module in (self.model.t3, self.model.s3gen, self.model.ve):
            if not all(p.device.type == "cuda" for p in module.parameters()):
                raise RuntimeError("A model component is not resident in CUDA")
        torch.cuda.synchronize()
        self.load_seconds = time.perf_counter() - started
        seed_golden()
        before = rng_snapshot()
        started = time.perf_counter()
        self.model.prepare_conditionals(str(self.root / VOICE.reference), exaggeration=VOICE.exaggeration)
        torch.cuda.synchronize()
        self.prepare_seconds = time.perf_counter() - started
        self.post_prepare_rng = rng_snapshot()
        self.rng_consumed = rng_changes(before, self.post_prepare_rng)
        self.initial_memory = cuda_memory()

    def _generate(self, text, original_reference=False):
        # Preserve the exact seed, and the position in each RNG after the
        # original reference preparation. Do not substitute a new seed.
        seed_golden()
        if not original_reference:
            restore_rng(self.post_prepare_rng)
        return self.model.generate(
            text, language_id=VOICE.language,
            audio_prompt_path=str(self.root / VOICE.reference) if original_reference else None,
            temperature=VOICE.temperature, exaggeration=VOICE.exaggeration,
            cfg_weight=VOICE.cfg_weight)

    def synthesize(self, text):
        with self.lock:
            torch.cuda.synchronize()
            before = cuda_memory()
            torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            wav = self._generate(text)
            torch.cuda.synchronize()
            audio = wav.squeeze().detach().cpu().numpy().copy()
            seconds = time.perf_counter() - started
            duration = len(audio) / self.model.sr
            if not len(audio) or not np.isfinite(audio).all():
                raise RuntimeError("Empty or non-finite audio")
            metrics = {"synthesis_seconds": seconds, "duration_seconds": duration,
                       "generation_over_audio": seconds / duration,
                       "memory_before": before, "memory_after": cuda_memory(),
                       "peak_allocated_gib": torch.cuda.max_memory_allocated() / 1024**3,
                       "peak_reserved_gib": torch.cuda.max_memory_reserved() / 1024**3}
            del wav
            return audio, metrics

    def validate_cached_voice(self, text):
        """Separate validation only: compare original preparation vs reuse.

        Production prepares the reference once. This check deliberately repeats
        the original path once to verify it before running the pipeline.
        """
        with self.lock:
            cached_conds = self.model.conds
            try:
                original = self._generate(text, original_reference=True).squeeze().numpy().copy()
                self.model.conds = cached_conds
                cached = self._generate(text).squeeze().numpy().copy()
                same_shape = original.shape == cached.shape
                delta = float(np.max(np.abs(original - cached))) if same_shape else None
                result = {"text": text, "same_shape": same_shape,
                          "exact_equal": same_shape and np.array_equal(original, cached),
                          "max_abs_difference": delta,
                          "duration_seconds": len(original) / self.model.sr}
                if not same_shape or not np.allclose(original, cached, rtol=0, atol=1e-5):
                    raise RuntimeError(f"Cached conditionals changed the reference audio: {result}")
                return result
            finally:
                self.model.conds = cached_conds
