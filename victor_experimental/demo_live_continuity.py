"""Real CUDA -> stretch -> bounded queue -> voice/room -> WinMM demo/export."""
from pathlib import Path
from dataclasses import asdict
import hashlib
import json
import os
import time
import uuid
import wave
import argparse

ROOT = Path(__file__).resolve().parents[1]
TEXT = ("Hola, César. Te estoy escuchando y ya puedo empezar a hablar contigo. "
        "Ahora preparo la siguiente frase mientras todavía escuchas la anterior. "
        "Conservo mi voz y los mismos ajustes que elegimos, para que podamos seguir hablando con calma.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--text', default=TEXT)
    parser.add_argument('--strategy', choices=['tranquilo','rapido'],default='tranquilo')
    args = parser.parse_args()
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    protected = [ROOT/'victor_habla.py',ROOT/'mi_voz.wav',ROOT/'src/chatterbox/models/s3gen/s3gen.py',
                 ROOT/'src/chatterbox/mtl_tts.py',ROOT/'checkpoints_es_es/s3gen.pt',
                 ROOT/'checkpoints_es_es/t3_es_es.safetensors']
    protected += [ROOT/'victor_experimental/room_tone_profile.json']
    hashes = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    print('Importing persistent CUDA engine; no downloads or package changes',flush=True)
    from .engine import VoiceEngine, VOICE, cuda_memory
    from .live_continuity import LiveContinuity
    print('Loading selected checkpoints',flush=True)
    engine = VoiceEngine(ROOT)
    print('STARTUP',json.dumps({'load':engine.load_seconds,'prepare':engine.prepare_seconds}),flush=True)
    run = 'victor_real_083_continuo_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    pipeline = LiveContinuity(engine,ROOT/'mi_voz.wav',capture=True)
    print('Starting real progressive response',flush=True)
    result = pipeline.run(args.text,strategy=args.strategy)
    if result['cancelled'] or result['errors']:
        raise RuntimeError(result['errors'] or 'Cancelled response cannot be exported as fully played')
    pcm = b''.join(result.pop('pcm_blocks'))
    assert len(pcm)//2 == result['output_frames']
    output = ROOT/(run+'.wav')
    with output.open('xb') as handle:
        with wave.open(handle,'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(pcm)
    with wave.open(str(output),'rb') as wav:
        assert wav.readframes(wav.getnframes()) == pcm
    synth = sum(r['synthesis_seconds'] for r in result['records'])
    voiced = sum(r['played_voice_seconds'] for r in result['records'])
    for path,original in hashes.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==original,path
    report = {'voice':asdict(VOICE),'speed':.83,'pitch_preservation':'WSOLA; same method accepted in comparisons',
              'trim_fade_unchanged':True,'startup':{'model_load_seconds':engine.load_seconds,
              'reference_prepare_seconds':engine.prepare_seconds},'synthesis_warmup':False,
              'live':result,'total_synthesis_seconds':synth,'total_stretched_voice_seconds':voiced,
              'generation_over_stretched_voice':synth/voiced,'generation_over_complete_output':synth/result['duration_seconds'],
              'peak_allocated_gib':max(r['peak_allocated_gib'] for r in result['records']),
              'peak_reserved_gib':max(r['peak_reserved_gib'] for r in result['records']),
              'final_cuda_memory':cuda_memory(),'protected_sha256':hashes,'audio':str(output),
              'device_pcm_sha256':hashlib.sha256(pcm).hexdigest(),
              'export':'Exact concatenation of successful waveOutWrite payloads; driver consumed same number of frames. Not loopback; no inferred silence inserted.',
              'room_tone_limits':'Synthetic Gaussian noise shaped by the approved preview spectral filter; no copied reference waveform or loops. Perceptual continuity requires listening.'}
    with (ROOT/(run+'.json')).open('x',encoding='utf-8') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2)
    print('RESULT',json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
