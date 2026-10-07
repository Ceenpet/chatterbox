"""Loopback protocol tests with simulated model, including LOADING access."""
import json
import secrets
import threading
import unittest
from urllib.request import Request
from urllib.error import HTTPError

from .voice_client import VoiceClient, ClientError
from .voice_http import VoiceHTTPServer
from .voice_service import VoiceService
from .test_voice_service import FakeEngine, FakePipeline, until


class Tests(unittest.TestCase):
    def setUp(self):
        self.gate = threading.Event()
        self.engine = FakeEngine()
        def factory(root):
            self.gate.wait(3)
            return self.engine
        self.service = VoiceService('.', engine_factory=factory, pipeline_factory=FakePipeline)
        self.token = secrets.token_urlsafe(32)
        self.server = VoiceHTTPServer(0, self.service, self.token)
        self.service.start()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.client = VoiceClient(self.token, f'http://127.0.0.1:{self.server.server_port}')

    def tearDown(self):
        self.gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.service.close()

    def ready(self):
        self.gate.set()
        until(lambda: self.client.status()['state'] == 'READY')

    def error(self, code, call):
        with self.assertRaises(ClientError) as raised: call()
        self.assertEqual(raised.exception.body['error']['code'], code)

    def test_http_available_during_loading_and_authorization(self):
        self.assertEqual(self.client.status()['state'], 'LOADING')
        self.error('UNAUTHORIZED', lambda: VoiceClient(secrets.token_urlsafe(32), self.client.url).status())
        self.ready()
        self.assertTrue(self.client.status()['can_accept'])
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_incremental_protocol_busy_idempotent_and_metrics(self):
        self.ready()
        self.client.create('id', 'Hola, ', 'rapido')
        self.error('BUSY', lambda: self.client.create('other'))
        self.client.append('id', 'César.', 1)
        self.assertEqual(self.engine.calls, [])
        self.client.append('id', ' Estoy aquí.', 2, True)
        done = self.client.wait('id', 3)
        self.assertEqual(done['state'], 'FINISHED')
        self.client.create('id', 'Hola, ', 'rapido')
        self.assertEqual(len(self.engine.calls), 1)
        self.error('UNKNOWN_ID', lambda: self.client.get('unknown'))
        self.client.request('POST', '/v1/shutdown', {})
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())

    def test_no_acoustic_fields_invalid_mode_and_body_limits(self):
        self.ready()
        for field in ('seed', 'temperature', 'speed', 'room_tone', 'model', 'reference', 'cfg_weight'):
            self.error('INVALID_FIELDS', lambda: self.client.request('POST', '/v1/responses',
                       {'response_id': 'bad', field: 1}))
        self.error('INVALID_STRATEGY', lambda: self.client.create('bad', strategy='auto'))
        self.error('INVALID_TEXT', lambda: self.client.create('bad', text='a'*20001))
        self.error('BODY_TOO_LARGE', lambda: self.client.create('bad', text='a'*70000))
        self.error('INVALID_FIELDS', lambda: self.client.request('POST', '/v1/responses', []))

    def test_json_errors_duplicate_keys_nonfinite_and_browser_origin(self):
        for raw in (b'{', b'{"response_id":"a","response_id":"b"}', b'{"response_id":NaN}'):
            req = Request(self.client.url+'/v1/responses', data=raw, method='POST',
                headers={'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/json'})
            with self.assertRaises(HTTPError) as raised: self.client.opener.open(req)
            self.assertEqual(json.load(raised.exception)['error']['code'], 'INVALID_JSON')
        req = Request(self.client.url+'/v1/status', headers={'Authorization': 'Bearer '+self.token,
                      'Origin': 'http://example.invalid'})
        with self.assertRaises(HTTPError) as raised: self.client.opener.open(req)
        self.assertEqual(raised.exception.code, 403)

    def test_utf8_escape_validation_and_wrong_fragment_fields(self):
        self.ready()
        req = Request(self.client.url+'/v1/responses',
            data=b'{"response_id":"bad","text":"\\ud800"}', method='POST',
            headers={'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as raised: self.client.opener.open(req)
        self.assertEqual(json.load(raised.exception)['error']['code'], 'INVALID_TEXT')
        self.client.create('parts')
        self.error('INVALID_FIELDS', lambda: self.client.request('POST', '/v1/responses/parts/text',
                   {'text': 'Hola', 'sequence': 1, 'speed': .83}))


if __name__ == '__main__': unittest.main()
