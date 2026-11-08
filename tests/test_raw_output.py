import bootstrap
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from astrbot_plugin_60s.core import HttpClient,Settings,ApiError
from astrbot_plugin_60s.manual import ManualService
from astrbot_plugin_60s.hot import HotService
from test_http import Response

class RawOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_preserves_all_text_formats_exactly(self):
        for fmt,body,mime in [('text',' 原始标题\n正文\n','text/plain'),('markdown','# 原始标题\n\n**正文**\n','text/markdown'),('json','{ "code": 200, "data": {"date":"2020-01-01"} }\n','application/json')]:
            calls=[]
            def get(url,**kw):calls.append((url,kw));return Response(body=body.encode(),headers={'Content-Type':mime})
            client=HttpClient(Settings(),SimpleNamespace(get=get))
            self.assertEqual(await client.fetch_raw('daily',fmt),body)
            self.assertEqual(calls[0][1]['params'],{'encoding':fmt})
    async def test_manual_old_text_and_json_have_no_wrapper(self):
        for kind in ('daily','history'):
            for fmt in ('text','json','markdown'):
                original='接口原文：旧日期 2000-01-01\n'
                fetch=AsyncMock(return_value=original)
                out=await ManualService(fetch,AsyncMock()).query(kind,fmt)
                self.assertEqual(out.text,original);fetch.assert_awaited_once_with(kind,fmt)
    async def test_hot_uses_requested_encoding_original_body(self):
        original='上游榜单\n1. a\n2. b\n'
        fetch=AsyncMock(return_value=original)
        out=await HotService(fetch).query('weibo','1','markdown')
        self.assertEqual(out.text,original)
        fetch.assert_awaited_once_with('weibo','markdown')
    async def test_binary_or_error_is_not_sent_as_text(self):
        for response in (Response(body=b'GIF89a',headers={'Content-Type':'image/gif'}),Response(body=b'{}',headers={'Content-Type':'application/octet-stream'}),Response(body=b'{"code":500,"message":"error"}',headers={'Content-Type':'application/json'})):
            client=HttpClient(Settings(),SimpleNamespace(get=lambda *a,**k:response))
            with self.assertRaises(ApiError):await client.fetch_raw('daily','text')

    async def test_all_hot_routes_and_formats_keep_original(self):
        from astrbot_plugin_60s.hot import BOARDS
        for board in BOARDS:
            for fmt in ('text','json','markdown'):
                original=f' upstream {board.key} {fmt}\n'
                fetch=AsyncMock(return_value=original)
                args=('12345','5',fmt) if board.key=='ncm_detail' else ('5',fmt,'')
                output=await HotService(fetch).query(board.key,*args)
                self.assertEqual(output.text,original)
                self.assertEqual(fetch.await_args.args[1],fmt)

    async def test_json_raw_is_reusable_for_internal_date_checks(self):
        raw='{ "code": 200, "data": { "date": "2026-11-08", "news": ["a"] } }\n'
        response=Response(body=raw.encode(),headers={'Content-Type':'application/json'})
        client=HttpClient(Settings(),SimpleNamespace(get=lambda *a,**kw:response))
        payload=await client.fetch('daily')
        self.assertEqual(payload['data']['date'],'2026-11-08')
        self.assertEqual(payload.raw_text,raw)

    async def test_text_query_params_and_plain_json_business_error(self):
        calls=[]
        def get(url,**kw):calls.append((url,kw));return Response(body=b'upstream',headers={'Content-Type':'text/plain'})
        client=HttpClient(Settings(base_url='http://127.0.0.1:8080'),SimpleNamespace(get=get))
        self.assertEqual(await client.fetch_raw_path('it-news/rank?type=week','markdown'),'upstream')
        self.assertEqual(calls[0][1]['params'],{'type':'week','encoding':'markdown'})
        response=Response(body=b'{"code":500}',headers={'Content-Type':'text/plain'})
        client.session=SimpleNamespace(get=lambda *a,**kw:response)
        with self.assertRaises(ApiError):await client.fetch_raw('daily','text')

    async def test_oversized_text_is_error_not_truncated_success(self):
        response=Response(body=b'a'*1_000_001,headers={'Content-Type':'text/plain'})
        client=HttpClient(Settings(),SimpleNamespace(get=lambda *a,**kw:response))
        with self.assertRaisesRegex(ApiError,'1MB'):await client.fetch_raw('history','text')
