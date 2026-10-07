"""Independent real service + separate stdlib client process. No Nexo required."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

from .voice_client import VoiceClient, ClientError

ROOT = Path(__file__).resolve().parents[1]
TEXT = ('Hola, César. Te estoy escuchando. '
        'Ahora preparo una respuesta un poco más extensa para comprobar que, mientras llega la siguiente frase, '
        'el fondo permanece discreto y la conversación mantiene su continuidad. '
        'Conservo mi voz y los mismos ajustes que elegimos, para que podamos seguir hablando con calma.')


def wait_for(predicate, timeout):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise TimeoutError('Expected service state did not arrive')


def client_test(port, output, strategy='tranquilo', text=TEXT, extra_text=None):
    TEXT = text
    client = VoiceClient(url=f'http://127.0.0.1:{port}')
    status_history = []
    started = time.monotonic()
    def ready():
        try:
            state = client.status()
        except OSError:
            return None
        if not status_history or status_history[-1] != state['state']:
            status_history.append(state['state'])
        if state['state'] == 'ERROR':
            raise RuntimeError(state['error'])
        return state if state['can_accept'] else None
    first_ready = wait_for(ready, 300)
    ready_elapsed = time.monotonic()-started
    client.create('cancelled-turn', TEXT, strategy, True)
    client.create('cancelled-turn', TEXT, strategy, True)  # Network retry must not duplicate.
    speaking = wait_for(lambda: client.get('cancelled-turn') if
        client.get('cancelled-turn')['state'] == 'SPEAKING' else None, 120)
    time.sleep(1.0)  # Hear the first segment, then interrupt while next is generating.
    cancel_started = time.monotonic()
    cancel_ack = client.cancel('cancelled-turn')
    cancel_history = [cancel_ack['state']]
    stopped_while_busy = False
    def cancelled():
        nonlocal stopped_while_busy
        turn = client.get('cancelled-turn')
        if cancel_history[-1] != turn['state']:
            cancel_history.append(turn['state'])
        if turn['state'] == 'CANCELLING' and turn['audio_stopped']:
            stopped_while_busy = True
        return turn if turn['state'] in ('CANCELLED', 'ERROR') else None
    first = wait_for(cancelled, 120)
    assert first['state'] == 'CANCELLED', first
    cancellation_drain_seconds = time.monotonic()-cancel_started
    assert client.status()['can_accept']
    # Incremental API tested without early synthesis: concatenate exactly TEXT.
    split = len(TEXT)//2
    client.create('finished-turn', TEXT[:split], strategy, False)
    assert client.get('finished-turn')['state'] == 'RECEIVING'
    client.append('finished-turn', TEXT[split:], 1, True)
    client.append('finished-turn', TEXT[split:], 1, True)  # Idempotent fragment retry.
    stale_cancel = client.cancel('cancelled-turn')
    assert stale_cancel['state'] == 'CANCELLED'
    second = client.wait('finished-turn', 180)
    assert second['state'] == 'FINISHED', second
    extra = None
    additional_turns = []
    if extra_text:
        for index, extra_input in enumerate(extra_text if isinstance(extra_text,list) else [extra_text]):
            name = 'extended-turn' if index == 0 else f'extended-turn-{index+1}'
            client.create(name, extra_input, strategy, True)
            result = client.wait(name, 300)
            assert result['state'] == 'FINISHED', result
            additional_turns.append(result)
        extra = additional_turns[0]
    final_status = client.status()
    assert final_status['engine_initializations'] == 1
    assert final_status['instance_id'] == first_ready['instance_id']
    assert second['metrics']['driver_position_frames'] == second['metrics']['output_frames']
    assert first['metrics']['segments'][0] == second['metrics']['segments'][0]
    report = {'service_process_separate_from_client': True, 'client_pid': os.getpid(),
              'loading_states_observed': status_history, 'ready_elapsed_from_client_seconds': ready_elapsed,
              'initial_status': first_ready, 'cancel_ack': cancel_ack,
              'cancel_states': cancel_history, 'audio_stopped_while_gpu_draining': stopped_while_busy,
              'cancel_until_fully_drained_seconds': cancellation_drain_seconds,
              'cancelled_turn': first, 'finished_turn': second, 'final_status': final_status,
              'extended_turn': extra,
              'additional_turns': additional_turns,
              'stale_cancel_did_not_affect_second': second['state'] == 'FINISHED'}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('REAL_CLIENT_OK', json.dumps({'load_seconds': final_status['load_seconds'],
        'first_audio_seconds': second['metrics']['first_device_restart_seconds'],
        'cancel_stop_seconds': first['metrics']['cancel_stop_latency_seconds'],
        'engine_initializations': final_status['engine_initializations'],
        'audio': second['export']['path'], 'report': str(output)}), flush=True)
    client.request('POST', '/v1/shutdown', {})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--client-only', type=Path)
    parser.add_argument('--strategy', choices=('tranquilo', 'rapido'), default='tranquilo')
    parser.add_argument('--text', default=TEXT)
    parser.add_argument('--extra-text', action='append')
    args = parser.parse_args()
    if args.client_only:
        client_test(args.port, args.client_only, args.strategy, args.text, args.extra_text)
        return
    protected = [ROOT/'victor_habla.py', ROOT/'mi_voz.wav', ROOT/'victor_experimental/engine.py',
                 ROOT/'victor_experimental/adaptive_segments.py', ROOT/'victor_experimental/synthetic_room_tone.py',
                 ROOT/'victor_experimental/room_tone_profile.json', ROOT/'victor_experimental/room_tone.py',
                 ROOT/'victor_experimental/speed_comparison.py', ROOT/'src/chatterbox/models/s3gen/s3gen.py']
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    directory = ROOT/'victor_service_runs'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    env = dict(os.environ, VICTOR_VOICE_TOKEN=secrets.token_urlsafe(32), PYTHONUNBUFFERED='1')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with (directory/'service.log').open('w', encoding='utf-8') as log:
        service = subprocess.Popen([sys.executable, '-B', '-m', 'victor_experimental.voice_http',
            '--port', str(args.port), '--capture-dir', str(directory), '--capture-stages'], cwd=ROOT,
            env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        try:
            subprocess.run([sys.executable, '-B', '-m', 'victor_experimental.demo_voice_service',
                '--port', str(args.port), '--client-only', str(directory/'report.json'),
                '--strategy', args.strategy, '--text', args.text] +
                ([item for text in (args.extra_text or []) for item in ('--extra-text', text)]),
                cwd=ROOT, env=env, check=True, timeout=600, creationflags=flags)
            assert service.wait(120) == 0
        finally:
            if service.poll() is None:
                try:
                    VoiceClient(env['VICTOR_VOICE_TOKEN'], f'http://127.0.0.1:{args.port}').request('POST', '/v1/shutdown', {})
                    service.wait(120)
                except Exception:
                    service.terminate()
                    service.wait(10)
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == value for p, value in before.items())
    print('REAL_SERVICE_STOPPED; protected baseline hashes unchanged', flush=True)


if __name__ == '__main__': main()
