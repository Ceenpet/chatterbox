"""Compare explicit adaptive strategies, same text/engine/settings/room tone.

python -B -m victor_experimental.demo_adaptive --compare
python -B -m victor_experimental.demo_adaptive --strategy tranquilo
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import wave

ROOT=Path(__file__).resolve().parents[1]
TEXT=('Hola, César. Te estoy escuchando. Estoy aquí para conversar contigo con calma. '
      'Ahora preparo la siguiente frase mientras todavía escuchas la anterior. '
      'Conservo mi voz y los mismos ajustes que elegimos, para que podamos seguir hablando con calma.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare',action='store_true')
    parser.add_argument('--strategy',choices=['tranquilo','rapido'],default='tranquilo')
    parser.add_argument('--text',default=TEXT)
    args=parser.parse_args()
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    paths=[ROOT/'victor_habla.py',ROOT/'mi_voz.wav',ROOT/'checkpoints_es_es/s3gen.pt',
           ROOT/'checkpoints_es_es/t3_es_es.safetensors',ROOT/'src/chatterbox/models/s3gen/s3gen.py',
           ROOT/'src/chatterbox/mtl_tts.py',ROOT/'victor_experimental/engine.py',
           ROOT/'victor_experimental/room_tone.py',ROOT/'victor_experimental/continuous_audio.py',
           ROOT/'victor_experimental/room_tone_profile.json']
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    print('Loading unchanged voice and CUDA engine',flush=True)
    from .engine import VoiceEngine,VOICE
    from .live_continuity import LiveContinuity
    engine=VoiceEngine(ROOT)
    print('STARTUP',json.dumps({'load':engine.load_seconds,'prepare':engine.prepare_seconds}),flush=True)
    warmup=None
    if args.compare:
        _,warmup=engine.synthesize('Hola, César. Te estoy escuchando.')
        print('WARMUP',warmup['synthesis_seconds'],flush=True)
    modes=['tranquilo','rapido'] if args.compare else [args.strategy]
    run='victor_adaptativo_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    summaries=[]
    for label,mode in zip(['A','B'],modes):
        print('RUN',mode,flush=True)
        pipeline=LiveContinuity(engine,ROOT/'mi_voz.wav',capture=True,strategy=mode)
        result=pipeline.run(args.text)
        if result['cancelled'] or result['errors']:
            raise RuntimeError(result['errors'] or 'Cancelled')
        pcm=b''.join(result.pop('pcm_blocks'))
        output=ROOT/(run+f'_{label}_{mode}.wav')
        with output.open('xb') as handle:
            with wave.open(handle,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(pcm)
        with wave.open(str(output),'rb') as wav:
            assert wav.readframes(wav.getnframes())==pcm
            assert wav.getnframes()==result['driver_position_frames']==result['output_frames']
        synth=sum(r['synthesis_seconds'] for r in result['records'])
        voice=sum(r['played_voice_seconds'] for r in result['records'])
        report={'mode':mode,'text':args.text,'voice':asdict(VOICE),'speed':.83,
                'pitch_preserved_method':'Same WSOLA','room_tone_and_trim_fade_unchanged':True,
                'startup':{'load_seconds':engine.load_seconds,'prepare_seconds':engine.prepare_seconds},
                'same_persistent_engine':True,'warmup_before_comparison':warmup,
                'live':result,'generation_over_stretched_voice':synth/voice,
                'generation_over_total_output':synth/result['duration_seconds'],
                'peak_allocated_gib':max(r['peak_allocated_gib'] for r in result['records']),
                'peak_reserved_gib':max(r['peak_reserved_gib'] for r in result['records']),
                'protected_hashes':hashes,'pcm_sha256':hashlib.sha256(pcm).hexdigest(),
                'audio':str(output),'export':'Exact PCM submitted and consumed by WinMM; not loopback recording'}
        for path,before in hashes.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==before,path
        report_path=ROOT/(run+f'_{label}_{mode}.json')
        with report_path.open('x',encoding='utf-8') as h:json.dump(report,h,ensure_ascii=False,indent=2)
        summary={'mode':mode,'audio':str(output),'report':str(report_path),
                 'first_voice_seconds':result['first_device_restart_seconds'],
                 'segments':result['segments'],
                 'synthesis_seconds':[r['synthesis_seconds'] for r in result['records']],
                 'played_seconds':[r['played_voice_seconds'] for r in result['records']],
                 'reserve_at_decisions_seconds':[r['segmentation']['snapshot']['voice_ahead_seconds'] for r in result['records']],
                 'target_chars':[r['segmentation']['target_chars'] for r in result['records']],
                 'generation_ratio':synth/voice,'duration_seconds':result['duration_seconds'],
                 'room_wait_seconds':result['room_only_wait_seconds'],
                 'room_active_seconds':result['room_nonzero_gain_seconds'],
                 'peak_vram_gib':report['peak_allocated_gib'],
                 'underruns':len(result['underruns_observed'])}
        summaries.append(summary);print('RESULT',json.dumps(summary,ensure_ascii=False),flush=True)
    with (ROOT/(run+'_comparison.json')).open('x',encoding='utf-8') as h:
        json.dump(summaries,h,ensure_ascii=False,indent=2)


if __name__=='__main__':main()
