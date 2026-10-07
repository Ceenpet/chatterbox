"""Stdlib-only external client: does not import any model or audio modules."""
import argparse
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler


class ClientError(Exception):
    def __init__(self, status, body):
        super().__init__(f'HTTP {status}: {body.get("error", {}).get("code", "ERROR")}')
        self.status, self.body = status, body


class VoiceClient:
    def __init__(self, token=None, url='http://127.0.0.1:8769', timeout=5):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Use an HTTP loopback URL with host 127.0.0.1')
        self.url, self.timeout = url.rstrip('/'), timeout
        self.token = token or os.environ.get('VICTOR_VOICE_TOKEN', '')
        if not self.token:
            raise ValueError('VICTOR_VOICE_TOKEN is required')
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        request = Request(self.url+path, data=data, method=method,
            headers={'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            raise ClientError(exc.code, json.load(exc)) from None

    def status(self):
        return self.request('GET', '/v1/status')

    def get(self, response_id):
        return self.request('GET', f'/v1/responses/{response_id}')

    def create(self, response_id, text='', strategy='tranquilo', end_of_response=False):
        return self.request('POST', '/v1/responses', dict(response_id=response_id,
            text=text, strategy=strategy, end_of_response=end_of_response))

    def append(self, response_id, text, sequence, end_of_response=False):
        return self.request('POST', f'/v1/responses/{response_id}/text',
            dict(text=text, sequence=sequence, end_of_response=end_of_response))

    def cancel(self, response_id):
        return self.request('POST', f'/v1/responses/{response_id}/cancel', {})

    def wait(self, response_id, timeout=300):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            turn = self.get(response_id)
            if turn['state'] in ('FINISHED', 'CANCELLED', 'ERROR'):
                return turn
            time.sleep(.05)
        raise TimeoutError('Turn did not reach a terminal state')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('operation', choices=['status', 'get', 'send', 'append', 'cancel', 'shutdown'])
    parser.add_argument('--id')
    parser.add_argument('--text', default='')
    parser.add_argument('--strategy', choices=['tranquilo', 'rapido'], default='tranquilo')
    parser.add_argument('--sequence', type=int)
    parser.add_argument('--end', action='store_true')
    parser.add_argument('--wait', action='store_true')
    args = parser.parse_args()
    client = VoiceClient(url=f'http://127.0.0.1:{args.port}')
    if args.operation in ('get', 'send', 'append', 'cancel') and not args.id:
        parser.error('--id is required')
    if args.operation == 'status': result = client.status()
    elif args.operation == 'get': result = client.get(args.id)
    elif args.operation == 'send': result = client.create(args.id, args.text, args.strategy, args.end)
    elif args.operation == 'append': result = client.append(args.id, args.text, args.sequence, args.end)
    elif args.operation == 'cancel': result = client.cancel(args.id)
    else: result = client.request('POST', '/v1/shutdown', {})
    if args.wait and args.id: result = client.wait(args.id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
