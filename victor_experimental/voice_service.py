"""Turn control only. One worker owns the persistent model and response runs."""
import copy
import queue
import re
import threading
import time
import uuid
from pathlib import Path

MAX_TEXT = 20000
MAX_TURNS = 256
MAX_FRAGMENTS = 256
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
TERMINAL = {'FINISHED', 'CANCELLED', 'ERROR'}


class ServiceError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def validate_id(response_id):
    if not isinstance(response_id, str) or not ID_PATTERN.fullmatch(response_id):
        raise ServiceError(400, 'INVALID_ID', 'response_id: 1-64 ASCII letters, digits, _, . or -')


def validate_text(text, final):
    if not isinstance(text, str) or len(text) > MAX_TEXT or '\x00' in text:
        raise ServiceError(400, 'INVALID_TEXT', f'text must be a string of at most {MAX_TEXT} characters without NUL')
    if type(final) is not bool:
        raise ServiceError(400, 'INVALID_END', 'end_of_response must be boolean')
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        raise ServiceError(400, 'INVALID_TEXT', 'text must contain valid Unicode characters')


def default_engine(root):
    from .engine import VoiceEngine
    return VoiceEngine(root)


def default_pipeline(engine, root, capture):
    from .live_continuity import LiveContinuity
    return LiveContinuity(engine, root/'mi_voz.wav', capture=capture)


class VoiceService:
    def __init__(self, root, *, engine_factory=default_engine,
                 pipeline_factory=default_pipeline, capture_dir=None, capture_stages=False):
        self.root = Path(root).resolve()
        self.engine_factory, self.pipeline_factory = engine_factory, pipeline_factory
        self.capture_dir = Path(capture_dir).resolve() if capture_dir else None
        if capture_stages and self.capture_dir is None:
            raise ValueError('Stage capture requires capture_dir')
        self.capture_stages = capture_stages
        self.instance_id = uuid.uuid4().hex
        self.condition = threading.Condition()
        self.events = queue.SimpleQueue()
        self.state = 'LOADING'
        self.error = None
        self.turns = {}
        self.active_id = None
        self.pipeline = None
        self.stopping = False
        self.initializations = 0
        self.unsafe_worker = False
        self.load_seconds = None
        self.warmup_metrics = None
        self.created = time.perf_counter()
        self.worker = threading.Thread(target=self._work, name='victor-service-worker')

    def start(self):
        self.worker.start()

    def _events(self):
        while True:
            try:
                response_id, event, stamp = self.events.get_nowait()
            except queue.Empty:
                return
            turn = self.turns.get(response_id)
            if turn is None or turn['state'] in TERMINAL:
                continue
            turn['events'][event] = stamp-turn['accepted_at']
            if event == 'audio_started':
                turn['audio_stopped'] = False
                if turn['state'] != 'CANCELLING':
                    turn['state'] = 'SPEAKING'
            elif event == 'audio_stopped':
                turn['audio_stopped'] = True

    def _view(self, turn):
        return copy.deepcopy({k: turn[k] for k in ('response_id', 'state', 'strategy',
                    'end_of_response', 'next_sequence', 'audio_stopped', 'events',
                    'metrics', 'error', 'export')})

    def status(self):
        with self.condition:
            self._events()
            return {'instance_id': self.instance_id, 'state': self.state,
                    'active_response_id': self.active_id,
                    'can_accept': self.state == 'READY' and self.active_id is None,
                    'engine_initializations': self.initializations,
                    'load_seconds': self.load_seconds, 'error': self.error,
                    'warmup_metrics': copy.deepcopy(self.warmup_metrics),
                    'active': None if self.active_id is None else self._view(self.turns[self.active_id])}

    def get(self, response_id):
        validate_id(response_id)
        with self.condition:
            self._events()
            if response_id not in self.turns:
                raise ServiceError(404, 'UNKNOWN_ID', 'Unknown response_id in this service instance')
            return self._view(self.turns[response_id])

    def create(self, response_id, text='', strategy='tranquilo', end_of_response=False):
        validate_id(response_id)
        validate_text(text, end_of_response)
        if strategy not in ('tranquilo', 'rapido'):
            raise ServiceError(400, 'INVALID_STRATEGY', 'strategy must be tranquilo or rapido')
        original = (text, strategy, end_of_response)
        with self.condition:
            self._events()
            if response_id in self.turns:
                turn = self.turns[response_id]
                if turn['original'] != original:
                    raise ServiceError(409, 'ID_CONFLICT', 'Existing response_id has a different initial request')
                return self._view(turn), False
            if self.state == 'BUSY':
                raise ServiceError(409, 'BUSY', 'Active turn is running or draining cancelled synthesis')
            if self.state != 'READY':
                raise ServiceError(503, 'NOT_READY', f'Service is {self.state}')
            if self.active_id is not None:
                raise ServiceError(409, 'BUSY', 'A turn is already open; cancel or finish it first')
            if len(self.turns) >= MAX_TURNS:
                raise ServiceError(409, 'TURN_LIMIT', 'Instance history limit reached; restart explicitly')
            if end_of_response and not text.strip():
                raise ServiceError(400, 'EMPTY_RESPONSE', 'Final response must contain text')
            turn = {'response_id': response_id, 'text': text, 'strategy': strategy,
                    'state': 'QUEUED' if end_of_response else 'RECEIVING',
                    'end_of_response': end_of_response, 'next_sequence': 1,
                    'original': original, 'fragments': {}, 'cancel': threading.Event(),
                    'accepted_at': time.perf_counter(), 'audio_stopped': True,
                    'events': {}, 'metrics': None, 'error': None, 'export': None}
            self.turns[response_id] = turn
            self.active_id = response_id
            self.condition.notify_all()
            return self._view(turn), True

    def append(self, response_id, text, sequence, end_of_response=False):
        validate_id(response_id)
        validate_text(text, end_of_response)
        if type(sequence) is not int or not 1 <= sequence <= MAX_FRAGMENTS:
            raise ServiceError(400, 'INVALID_SEQUENCE', f'sequence must be 1-{MAX_FRAGMENTS}')
        with self.condition:
            turn = self.turns.get(response_id)
            if turn is None:
                raise ServiceError(404, 'UNKNOWN_ID', 'Unknown response_id')
            fragment = (text, end_of_response)
            if sequence in turn['fragments']:
                if turn['fragments'][sequence] != fragment:
                    raise ServiceError(409, 'SEQUENCE_CONFLICT', 'Duplicate sequence has different content')
                return self._view(turn)
            if self.stopping or turn['state'] != 'RECEIVING':
                raise ServiceError(409, 'TURN_CLOSED', 'Turn no longer accepts text')
            if sequence != turn['next_sequence']:
                raise ServiceError(409, 'OUT_OF_ORDER', f'Expected sequence {turn["next_sequence"]}')
            combined = turn['text']+text
            if len(combined) > MAX_TEXT:
                raise ServiceError(400, 'TEXT_LIMIT', f'Accumulated text exceeds {MAX_TEXT} characters')
            if end_of_response and not combined.strip():
                raise ServiceError(400, 'EMPTY_RESPONSE', 'Final response must contain text')
            turn['text'] = combined
            turn['fragments'][sequence] = fragment
            turn['next_sequence'] += 1
            turn['end_of_response'] = end_of_response
            if end_of_response:
                turn['state'] = 'QUEUED'
                self.condition.notify_all()
            return self._view(turn)

    def cancel(self, response_id):
        validate_id(response_id)
        with self.condition:
            self._events()
            turn = self.turns.get(response_id)
            if turn is None:
                raise ServiceError(404, 'UNKNOWN_ID', 'Unknown response_id')
            if turn['state'] in TERMINAL:
                return self._view(turn)
            turn['cancel'].set()
            if turn['state'] in ('RECEIVING', 'QUEUED'):
                turn['state'] = 'CANCELLED'
                self.active_id = None
            else:
                turn['state'] = 'CANCELLING'
                if self.pipeline is not None:
                    self.pipeline.cancel()  # Only sets events; never touches WinMM here.
            self.condition.notify_all()
            return self._view(turn)

    def _export(self, turn, result):
        blocks = result.pop('pcm_blocks', None)
        if self.capture_dir is None or blocks is None:
            return None
        import hashlib
        import wave
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self.capture_dir/f'victor_service_{self.instance_id}_{turn["response_id"]}.wav'
        pcm = b''.join(blocks)
        assert len(pcm)//2 == result['output_frames']
        with path.open('xb') as handle:
            with wave.open(handle, 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
                wav.writeframes(pcm)
        return {'path': str(path), 'pcm_sha256': hashlib.sha256(pcm).hexdigest(),
                'note': 'Exact submitted PCM; cancellation may discard submitted but not yet played frames'}

    def _work(self):
        try:
            started = time.perf_counter()
            phase = 'LOAD_FAILED'
            engine = self.engine_factory(self.root)
            with self.condition:
                self.initializations += 1
            phase = 'WARMUP_FAILED'
            self.warmup_metrics = engine.warm_up()
            with self.condition:
                self.load_seconds = time.perf_counter()-started
                self.state = 'STOPPING' if self.stopping else 'READY'
                self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = {'code': phase, 'message': str(exc)}
                self.state = 'ERROR' if not self.stopping else 'STOPPED'
                self.condition.notify_all()
            return
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopping or
                    (self.active_id is not None and self.turns[self.active_id]['state'] == 'QUEUED'))
                if self.stopping:
                    self.state = 'STOPPED'
                    self.condition.notify_all()
                    return
                turn = self.turns[self.active_id]
                turn['state'] = 'GENERATING'
                self.state = 'BUSY'
                response_id = turn['response_id']
            result, failure, exported = None, None, None
            stages = []
            try:
                pipeline = self.pipeline_factory(engine, self.root, self.capture_dir is not None)
                if self.capture_stages:
                    pipeline.audio_observer = lambda index, text, stage, audio: stages.append((index, text, stage, audio.copy()))
                with self.condition:
                    self.pipeline = pipeline
                # Queue-only observer; no HTTP/JSON/service locks on audio path.
                result = pipeline.run(turn['text'], strategy=turn['strategy'],
                    on_event=lambda event, stamp: self.events.put((response_id, event, stamp)),
                    cancel_event=turn['cancel'])
                exported = self._export(turn, result)
                if exported is not None and stages:
                    import numpy as np
                    import soundfile as sf
                    exported['stages'] = []
                    for index, text, stage, audio in stages:
                        name = f'victor_service_{self.instance_id}_{response_id}_{index+1}_{stage}'
                        path = self.capture_dir/(name+'.wav')
                        with path.open('xb') as handle:
                            sf.write(handle, audio, 24000, format='WAV', subtype='FLOAT')
                        with path.with_suffix('.npy').open('xb') as handle:
                            np.save(handle, audio)
                        exported['stages'].append({'index': index, 'text': text, 'stage': stage,
                            'path':str(path), 'duration_seconds':len(audio)/24000})
                if result.get('errors'):
                    failure = {'code': 'TURN_FAILED', 'message': '; '.join(result['errors'])}
            except Exception as exc:
                failure = {'code': 'TURN_FAILED', 'message': str(exc)}
            with self.condition:
                self._events()
                turn['metrics'] = result
                turn['export'] = exported
                turn['error'] = failure
                turn['audio_stopped'] = True
                # run() has returned only after audio closure and producer join.
                turn['state'] = ('ERROR' if failure else 'CANCELLED' if
                    turn['cancel'].is_set() or (result and result.get('cancelled')) else 'FINISHED')
                self.pipeline = None
                self.active_id = None
                unsafe = failure and 'In-flight generation did not finish' in failure['message']
                self.state = 'ERROR' if unsafe else 'STOPPING' if self.stopping else 'READY'
                if unsafe:
                    self.unsafe_worker = True
                    self.error = {'code': 'WORKER_NOT_DRAINED', 'message': 'Restart service after pending synthesis is stopped'}
                self.condition.notify_all()
                if unsafe:
                    return  # Never admit another synthesis over an undrained worker.

    def request_shutdown(self):
        with self.condition:
            self.stopping = True
            self.state = 'STOPPING'
            active = self.active_id
        if active is not None:
            self.cancel(active)
        with self.condition:
            self.condition.notify_all()

    def close(self, timeout=120):
        self.request_shutdown()
        self.worker.join(timeout)
        if self.worker.is_alive():
            raise RuntimeError('Worker still draining GPU work; shutdown not complete')
        with self.condition:
            if self.unsafe_worker:
                self.state = 'ERROR'
                raise RuntimeError('Undrained synthesis worker: cannot certify clean shutdown')
            self.state = 'STOPPED'
