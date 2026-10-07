"""Loopback HTTP/JSON transport. Request threads never run the voice engine."""
import argparse
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .voice_service import VoiceService, ServiceError

MAX_JSON = 65536


def fields(body, allowed, required=()):
    if not isinstance(body, dict) or set(body)-set(allowed) or not set(required) <= set(body):
        raise ServiceError(400, 'INVALID_FIELDS', 'Missing required fields or unknown fields')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


class VoiceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port, service, token):
        if not isinstance(token, str) or not 32 <= len(token) <= 256 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError('VICTOR_VOICE_TOKEN must be 32-256 ASCII characters without whitespace')
        self.service, self.token = service, token
        super().__init__(('127.0.0.1', port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'VictorVoice/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # Neither requests, tokens nor conversation text are logged.

    def _reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _body(self):
        if self.headers.get('Transfer-Encoding') is not None:
            raise ServiceError(400, 'TRANSFER_ENCODING', 'Chunked requests are not supported')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1:
            raise ServiceError(411, 'CONTENT_LENGTH', 'Exactly one Content-Length is required')
        try:
            length = int(lengths[0])
        except ValueError:
            raise ServiceError(400, 'CONTENT_LENGTH', 'Invalid Content-Length')
        if length > MAX_JSON:
            raise ServiceError(413, 'BODY_TOO_LARGE', f'Maximum JSON size is {MAX_JSON} bytes')
        if length < 2:
            raise ServiceError(400, 'INVALID_JSON', 'Empty or invalid JSON body')
        if self.headers.get_content_type() != 'application/json':
            raise ServiceError(415, 'CONTENT_TYPE', 'Use application/json')
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('Incomplete body')
            return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
        except (ValueError, UnicodeError, RecursionError):
            raise ServiceError(400, 'INVALID_JSON', 'Malformed JSON, duplicate keys or non-finite value')
        except TimeoutError:
            raise ServiceError(408, 'BODY_TIMEOUT', 'Request body timed out')

    def _dispatch(self):
        authorization = self.headers.get_all('Authorization', [])
        expected = 'Bearer '+self.server.token
        if len(authorization) != 1 or not authorization[0].isascii() or not hmac.compare_digest(authorization[0], expected):
            raise ServiceError(401, 'UNAUTHORIZED', 'A valid local bearer token is required')
        if self.headers.get('Origin') is not None:
            raise ServiceError(403, 'BROWSER_ORIGIN', 'Browser-origin requests are not supported')
        url = urlsplit(self.path)
        if url.query or url.fragment:
            raise ServiceError(400, 'INVALID_PATH', 'Query strings and fragments are not supported')
        parts = url.path.split('/')
        service = self.server.service
        if self.command == 'GET':
            if url.path == '/v1/status':
                return 200, service.status()
            if len(parts) == 4 and parts[1:3] == ['v1', 'responses']:
                return 200, service.get(parts[3])
        elif self.command == 'POST':
            body = self._body()
            if url.path == '/v1/responses':
                fields(body, ('response_id', 'text', 'strategy', 'end_of_response'), ('response_id',))
                result, new = service.create(**body)
                return (201 if new else 200), result
            if len(parts) == 5 and parts[1:3] == ['v1', 'responses']:
                if parts[4] == 'text':
                    fields(body, ('text', 'sequence', 'end_of_response'), ('text', 'sequence'))
                    return 200, service.append(parts[3], **body)
                if parts[4] == 'cancel':
                    fields(body, ())
                    return 200, service.cancel(parts[3])
            if url.path == '/v1/shutdown':
                fields(body, ())
                service.request_shutdown()
                # shutdown must not run in serve_forever's own thread.
                threading.Thread(target=self.server.shutdown, name='victor-http-shutdown', daemon=True).start()
                return 202, {'state': 'STOPPING'}
        raise ServiceError(404, 'UNKNOWN_ENDPOINT', 'Unknown endpoint')

    def _handle(self):
        try:
            status, result = self._dispatch()
        except ServiceError as exc:
            status, result = exc.status, {'error': {'code': exc.code, 'message': str(exc)}}
        except Exception:
            status, result = 500, {'error': {'code': 'INTERNAL_ERROR', 'message': 'Request failed'}}
        self._reply(status, result)

    do_GET = _handle
    do_POST = _handle

    def do_OPTIONS(self):
        self._reply(405, {'error': {'code': 'METHOD_NOT_ALLOWED', 'message': 'Use GET or POST'}})

    do_PUT = do_OPTIONS
    do_DELETE = do_OPTIONS
    do_PATCH = do_OPTIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--capture-dir', type=Path, help='Optional exact submitted PCM exports; off by default')
    parser.add_argument('--capture-stages', action='store_true', help='Optional raw/processed capture; requires --capture-dir')
    args = parser.parse_args()
    token = os.environ.get('VICTOR_VOICE_TOKEN', '')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    service = VoiceService(Path(__file__).resolve().parents[1], capture_dir=args.capture_dir, capture_stages=args.capture_stages)
    server = VoiceHTTPServer(args.port, service, token)
    # Socket is bound before model imports/loading begin on the sole worker.
    service.start()
    print(f'HTTP http://127.0.0.1:{server.server_port} LOADING', flush=True)
    try:
        server.serve_forever(poll_interval=.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()
        print('STOPPED', flush=True)


if __name__ == '__main__':
    main()
