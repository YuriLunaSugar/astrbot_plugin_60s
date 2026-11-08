import bootstrap  # Expose the repository root as the plugin package.
import json
import unittest
from types import SimpleNamespace

from astrbot_plugin_60s.core import ApiError, HttpClient, Settings


class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self.body = body or b'{"code":200,"data":{}}'
        self.headers = headers or {}
        self.content = self
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.exited = True

    async def iter_chunked(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_and_endpoint(self):
        response = Response(body=json.dumps({"code": 200, "data": {"news": ["x" * 70000]}}).encode())
        calls = []
        def get(url, **kw):
            calls.append((url, kw))
            return response
        client = HttpClient(Settings(), SimpleNamespace(get=get))
        result = await client.fetch("daily")
        self.assertEqual(len(result["data"]["news"][0]), 70000)
        self.assertTrue(calls[0][0].endswith("/v2/60s"))
        self.assertFalse(calls[0][1]["allow_redirects"])
        self.assertTrue(response.exited)

    async def test_status_json_and_size_errors(self):
        for response in (Response(503), Response(302), Response(body=b'bad'), Response(body=b'x' * 1_000_001)):
            client = HttpClient(Settings(), SimpleNamespace(get=lambda *a, **kw: response))
            with self.assertRaises(ApiError):
                await client.fetch("history")
            self.assertTrue(response.exited)

    async def test_429_header(self):
        response = Response(429, headers={"Retry-After": "3600"})
        client = HttpClient(Settings(), SimpleNamespace(get=lambda *a, **kw: response))
        with self.assertRaises(ApiError) as error:
            await client.fetch("daily")
        self.assertEqual(error.exception.retry_after, 3600)
