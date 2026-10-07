"""Deterministic simulated engine/turn tests; no GPU or audio imports."""
import threading
import time
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path

from .voice_service import VoiceService, ServiceError


def until(predicate, timeout=3):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.005)
    raise AssertionError('Timed out')


class FakeEngine:
    def warm_up(self):
        return {'synthesis_seconds': .01}

    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.draining = threading.Event()
        self.drain_release = threading.Event()
        self.drain_release.set()
        self.concurrent = 0
        self.peak_concurrent = 0


class FakePipeline:
    def __init__(self, engine, root, capture):
        self.engine = engine
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self, text, strategy=None, *, on_event=None, cancel_event=None):
        engine = self.engine
        engine.concurrent += 1
        engine.peak_concurrent = max(engine.concurrent, engine.peak_concurrent)
        try:
            engine.calls.append((text, strategy))
            engine.started.set()
            if text == 'FAIL':
                raise RuntimeError('simulated turn failure')
            if cancel_event.is_set():
                self.cancel()
            on_event('audio_started', time.perf_counter())
            while not engine.release.wait(.005) and not self.cancelled.is_set():
                pass
            on_event('audio_stopped', time.perf_counter())
            if self.cancelled.is_set() or cancel_event.is_set():
                engine.draining.set()
                if not engine.drain_release.wait(3):
                    raise RuntimeError('Test drain timed out')
            return {'cancelled': self.cancelled.is_set() or cancel_event.is_set(),
                    'errors': [], 'records': [{'synthesis_seconds': .01}], 'pcm_blocks': None}
        finally:
            engine.concurrent -= 1


class Tests(unittest.TestCase):
    def test_opt_in_stage_exports_and_disabled_default(self):
        import numpy as np
        class CapturingPipeline(FakePipeline):
            def run(self,*args,**kwargs):
                audio=np.ones(10,dtype=np.float32)*.1
                self.audio_observer(0,args[0],'raw',audio)
                self.audio_observer(0,args[0],'processed',audio.copy())
                result=super().run(*args,**kwargs)
                result.update(pcm_blocks=[b'\0\0'*10],output_frames=10)
                return result
        test_root=Path(__file__).resolve().parents[1]/'victor_service_runs'
        test_root.mkdir(exist_ok=True)
        with nullcontext(str(test_root/('test_export_'+uuid.uuid4().hex))) as directory:
            service=VoiceService('.',engine_factory=lambda root:FakeEngine(),pipeline_factory=CapturingPipeline,
                capture_dir=directory,capture_stages=True)
            service.start()
            try:
                until(lambda:service.status()['state']=='READY')
                service.create('capture','Hola.',end_of_response=True)
                turn=self.final(service,'capture')
                self.assertEqual(turn['state'],'FINISHED',turn['error'])
                self.assertEqual([s['stage'] for s in turn['export']['stages']],['raw','processed'])
                for stage in turn['export']['stages']:
                    self.assertTrue(Path(stage['path']).exists())
                    np.testing.assert_array_equal(np.load(Path(stage['path']).with_suffix('.npy')),np.ones(10,dtype=np.float32)*.1)
            finally:service.close()
        self.assertFalse(VoiceService('.').capture_stages)
        with self.assertRaises(ValueError):VoiceService('.',capture_stages=True)

    def make(self, load_gate=None):
        engine = FakeEngine()
        def factory(root):
            if load_gate is not None:
                load_gate.wait(3)
            return engine
        service = VoiceService('.', engine_factory=factory, pipeline_factory=FakePipeline)
        service.start()
        self.addCleanup(service.close)
        if load_gate is None:
            until(lambda: service.status()['state'] == 'READY')
        return service, engine

    def final(self, service, response_id):
        return until(lambda: service.get(response_id) if service.get(response_id)['state'] in
                     ('FINISHED', 'CANCELLED', 'ERROR') else None)

    def error(self, code, call):
        with self.assertRaises(ServiceError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)

    def test_loading_ready_and_single_instance(self):
        gate = threading.Event()
        service, engine = self.make(gate)
        self.assertEqual(service.status()['state'], 'LOADING')
        self.error('NOT_READY', lambda: service.create('early', 'Hola', end_of_response=True))
        gate.set()
        until(lambda: service.status()['state'] == 'READY')
        for name in ('one', 'two'):
            service.create(name, 'Hola', end_of_response=True)
            self.assertEqual(self.final(service, name)['state'], 'FINISHED')
        self.assertEqual(service.status()['engine_initializations'], 1)
        self.assertEqual(engine.peak_concurrent, 1)

    def test_load_error_and_shutdown(self):
        def fail(root): raise RuntimeError('simulated load failure')
        service = VoiceService('.', engine_factory=fail, pipeline_factory=FakePipeline)
        service.start()
        until(lambda: service.status()['state'] == 'ERROR')
        self.assertEqual(service.status()['error']['code'], 'LOAD_FAILED')
        service.close()
        self.assertEqual(service.status()['state'], 'STOPPED')

    def test_warmup_loading_and_failure(self):
        gate = threading.Event()
        class WarmEngine(FakeEngine):
            def warm_up(self):
                gate.wait(3)
                raise RuntimeError('warmup failed')
        service = VoiceService('.', engine_factory=lambda root: WarmEngine(), pipeline_factory=FakePipeline)
        service.start()
        self.addCleanup(service.close)
        until(lambda: service.status()['engine_initializations'] == 1)
        self.assertEqual(service.status()['state'], 'LOADING')
        gate.set()
        until(lambda: service.status()['state'] == 'ERROR')
        self.assertEqual(service.status()['error']['code'], 'WARMUP_FAILED')

    def test_incremental_order_and_retries_exactly_once(self):
        service, engine = self.make()
        request = dict(response_id='parts', text='Hola, ', strategy='rapido')
        self.assertTrue(service.create(**request)[1])
        self.assertFalse(service.create(**request)[1])
        self.error('BUSY', lambda: service.create('other'))
        self.error('OUT_OF_ORDER', lambda: service.append('parts', 'bad', 2))
        service.append('parts', 'César.', 1)
        service.append('parts', 'César.', 1)
        self.assertEqual(engine.calls, [])
        self.error('SEQUENCE_CONFLICT', lambda: service.append('parts', 'different', 1))
        service.append('parts', ' Estoy aquí.', 2, True)
        self.assertEqual(self.final(service, 'parts')['state'], 'FINISHED')
        service.append('parts', ' Estoy aquí.', 2, True)
        service.create(**request)
        self.assertEqual(engine.calls, [('Hola, César. Estoy aquí.', 'rapido')])
        self.error('TURN_CLOSED', lambda: service.append('parts', 'new', 3))
        self.error('ID_CONFLICT', lambda: service.create('parts', 'different'))

    def test_cancel_drain_busy_and_old_cancel_cannot_touch_next(self):
        service, engine = self.make()
        engine.release.clear()
        engine.drain_release.clear()
        service.create('old', 'Primera', end_of_response=True)
        self.assertTrue(engine.started.wait(2))
        self.assertEqual(service.cancel('old')['state'], 'CANCELLING')
        self.assertTrue(engine.draining.wait(2))
        status = service.status()
        self.assertEqual(status['state'], 'BUSY')
        self.assertTrue(status['active']['audio_stopped'])
        self.error('BUSY', lambda: service.create('too-soon', 'Hola', end_of_response=True))
        engine.drain_release.set()
        self.assertEqual(self.final(service, 'old')['state'], 'CANCELLED')
        self.assertFalse(service.create('old', 'Primera', end_of_response=True)[1])
        engine.release.set()
        service.create('new', 'Segunda', end_of_response=True)
        service.cancel('old')
        self.assertEqual(self.final(service, 'new')['state'], 'FINISHED')
        self.assertEqual(len(engine.calls), 2)

    def test_receiving_cancel_unknown_invalid_inputs_and_limits(self):
        service, engine = self.make()
        service.create('receiving', 'Hola')
        self.assertEqual(service.cancel('receiving')['state'], 'CANCELLED')
        self.assertEqual(engine.calls, [])
        self.error('UNKNOWN_ID', lambda: service.cancel('absent'))
        self.error('UNKNOWN_ID', lambda: service.get('absent'))
        self.error('INVALID_ID', lambda: service.create('../path'))
        self.error('INVALID_STRATEGY', lambda: service.create('bad', strategy='automatico'))
        self.error('INVALID_END', lambda: service.create('bad', end_of_response=1))
        self.error('INVALID_TEXT', lambda: service.create('bad', text='a'*20001))
        service.create('limit', text='a'*19999)
        self.error('TEXT_LIMIT', lambda: service.append('limit', 'aaa', 1))
        self.error('INVALID_SEQUENCE', lambda: service.append('limit', 'a', True))

    def test_turn_error_recovery_and_metrics(self):
        service, engine = self.make()
        service.create('bad', 'FAIL', end_of_response=True)
        failed = self.final(service, 'bad')
        self.assertEqual(failed['state'], 'ERROR')
        self.assertEqual(failed['error']['code'], 'TURN_FAILED')
        self.assertTrue(service.status()['can_accept'])
        service.create('good', 'Hola', end_of_response=True)
        done = self.final(service, 'good')
        self.assertEqual(done['metrics']['records'][0]['synthesis_seconds'], .01)
        self.assertIn('audio_started', done['events'])
        self.assertIn('audio_stopped', done['events'])

    def test_shutdown_during_turn_and_during_loading(self):
        service, engine = self.make()
        engine.release.clear()
        service.create('active', 'Hola', end_of_response=True)
        self.assertTrue(engine.started.wait(2))
        service.close()
        self.assertEqual(service.get('active')['state'], 'CANCELLED')
        self.assertFalse(service.worker.is_alive())
        gate = threading.Event()
        loading, _ = self.make(gate)
        loading.request_shutdown()
        gate.set()
        loading.close()
        self.assertEqual(loading.status()['state'], 'STOPPED')

    def test_cancel_while_pipeline_is_being_constructed(self):
        constructing, release = threading.Event(), threading.Event()
        engine = FakeEngine()
        def pipeline_factory(*args):
            constructing.set()
            release.wait(3)
            return FakePipeline(*args)
        service = VoiceService('.', engine_factory=lambda root: engine, pipeline_factory=pipeline_factory)
        service.start()
        self.addCleanup(service.close)
        until(lambda: service.status()['state'] == 'READY')
        service.create('race', 'Hola', end_of_response=True)
        self.assertTrue(constructing.wait(2))
        self.assertEqual(service.cancel('race')['state'], 'CANCELLING')
        release.set()
        self.assertEqual(self.final(service, 'race')['state'], 'CANCELLED')

    def test_undrained_worker_quarantines_service(self):
        class Undrained(FakePipeline):
            def run(self, *args, **kwargs):
                raise RuntimeError('In-flight generation did not finish after cancellation')
        service = VoiceService('.', engine_factory=lambda root: FakeEngine(), pipeline_factory=Undrained)
        service.start()
        until(lambda: service.status()['state'] == 'READY')
        service.create('unsafe', 'Hola', end_of_response=True)
        self.assertEqual(self.final(service, 'unsafe')['state'], 'ERROR')
        self.assertEqual(service.status()['state'], 'ERROR')
        self.error('NOT_READY', lambda: service.create('later', 'Hola', end_of_response=True))
        with self.assertRaisesRegex(RuntimeError, 'cannot certify clean shutdown'):
            service.close()
        self.assertFalse(service.worker.is_alive())


if __name__ == '__main__': unittest.main()
