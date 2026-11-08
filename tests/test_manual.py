import bootstrap
import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from astrbot_plugin_60s.core import ApiError
from astrbot_plugin_60s.manual import ManualService
from test_core import daily, history
from astrbot_plugin_60s.image_api import ImageResult
from astrbot_plugin_60s.formats import Output


class ManualTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now=datetime(2026,11,8,1,tzinfo=UTC)
        self.fetch=AsyncMock(side_effect=lambda kind,fmt: '原文 2026-11-08' if kind=='daily' else '历史原文 11-8')
        self.image=AsyncMock(return_value=ImageResult('2026-11-08',Output('模拟图片日期',b'fake-safe-image')))
        self.service=ManualService(self.fetch,self.image,timezone='Asia/Shanghai',clock=lambda:self.now)

    async def test_repeated_queries_each_fetch_once_without_result_cache(self):
        for _ in range(2):
            result=await self.service.query('daily','text')
            self.assertIn('2026-11-08',result.text)
        self.assertEqual([c.args[0] for c in self.fetch.await_args_list],['daily','daily'])
        self.image.assert_not_awaited()

    async def test_default_formats(self):
        payload=daily();payload['data']['image']='https://mmbiz.qpic.cn/demo.png'
        self.fetch.side_effect=lambda kind,fmt: '原文' if kind=='daily' else '历史原文 11-8'
        result=await self.service.query('daily')
        self.assertEqual(result.image,b'fake-safe-image')
        self.assertEqual(self.fetch.await_count,0)
        self.image.assert_awaited_once_with()
        result=await self.service.query('history')
        self.assertIsNone(result.image)
        self.assertIn('11-8',result.text)
        self.assertEqual(self.fetch.await_count,1)

    async def test_stale_json_keeps_actual_date_and_valid_json(self):
        original=json.dumps(daily('2026-11-07'),ensure_ascii=False,indent=2)+'\n'
        self.fetch.side_effect=lambda *args:original
        result=await self.service.query('daily','json')
        doc=json.loads(result.text)
        self.assertEqual(doc['data']['date'],'2026-11-07')
        self.assertEqual(result.text,original)
        self.assertEqual(self.fetch.await_count,1)

    async def test_history_different_date_and_leap_day_are_labeled(self):
        payload=history();payload['data']['date']='2-29'
        self.fetch.side_effect=lambda *args:'# 历史原文 2-29\n'
        result=await self.service.query('history','markdown')
        self.assertIn('2-29',result.text)
        self.assertEqual(result.text,'# 历史原文 2-29\n')
        self.assertEqual(self.fetch.await_count,1)

    async def test_help_and_invalid_format_do_not_fetch(self):
        result=await self.service.query('daily','帮助')
        self.assertIn('/60s',result.text)
        for kind,fmt in [('history','image'),('daily','off'),('daily','2025-01-01')]:
            with self.assertRaises(ApiError):await self.service.query(kind,fmt)
        self.fetch.assert_not_awaited()

    async def test_429_no_retry_and_future_manual_queries_respect_delay(self):
        self.fetch.side_effect=ApiError('HTTP 429',900)
        with self.assertRaisesRegex(ApiError,'900'):await self.service.query('daily','text')
        with self.assertRaisesRegex(ApiError,'限流'):await self.service.query('daily','text')
        self.assertEqual(self.fetch.await_count,1)
        self.fetch.side_effect=lambda *args:'历史原文'
        await self.service.query('history')
        self.now+=timedelta(seconds=900)
        self.fetch.side_effect=lambda *args:'日报原文'
        await self.service.query('daily','text')
        self.assertEqual(self.fetch.await_count,3)

    async def test_fetch_failure_and_missing_image_never_refetch(self):
        self.fetch.side_effect=OSError('network')
        with self.assertRaises(OSError):await self.service.query('daily','text')
        self.assertEqual(self.fetch.await_count,1)
        self.fetch.side_effect=lambda *args:'日报原文'
        result=await self.service.query('daily')
        self.assertEqual(result.image,b'fake-safe-image')
        self.assertEqual(self.fetch.await_count,1)
        self.image.assert_awaited_once_with()

    async def test_empty_or_nontext_success_value_rejected_once(self):
        for payload in ('', '  ', b'not text', {'data':'not raw'}):
            self.fetch.side_effect=lambda *args,p=payload:p
            with self.assertRaises(ApiError):await self.service.query('daily','text')
        self.assertEqual(self.fetch.await_count,4)

    async def test_image_failure_no_text_fallback_or_extra_api_request(self):
        payload=daily();payload['data']['image']='https://mmbiz.qpic.cn/demo.png'
        self.fetch.side_effect=lambda *args:'# 历史原文 2-29\n'
        self.image.side_effect=ApiError('图片 HTTP 429',900)
        with self.assertRaisesRegex(ApiError,'900'):await self.service.query('daily')
        with self.assertRaisesRegex(ApiError,'限流'):await self.service.query('daily')
        self.assertEqual(self.fetch.await_count,0)
        self.assertEqual(self.image.await_count,1)
        await self.service.query('daily','text')
        self.assertEqual(self.fetch.await_count,1)

    async def test_server_limit_reader_does_not_mutate_scheduled_state(self):
        state={'api:daily':self.now.timestamp()+60}
        self.service.limit_reader=lambda kind,fmt:state.get('api:'+kind,0)
        before=dict(state)
        with self.assertRaisesRegex(ApiError,'限流'):await self.service.query('daily','text')
        self.fetch.assert_not_awaited()
        self.assertEqual(state,before)

    async def test_timeout_and_cancellation_stop_without_retry(self):
        import asyncio
        started=asyncio.Event();stopped=asyncio.Event()
        async def waiting(*args):
            started.set()
            try:await asyncio.Event().wait()
            finally:stopped.set()
        self.fetch.side_effect=waiting
        self.service.timeout=0.01
        with self.assertRaises(TimeoutError):await self.service.query('daily','text')
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.fetch.await_count,1)
        started.clear();stopped.clear();self.service.timeout=20
        task=asyncio.create_task(self.service.query('daily','text'))
        await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.fetch.await_count,2)

    async def test_http_one_image_api_without_json_when_filename_has_date(self):
        from types import SimpleNamespace
        from astrbot_plugin_60s.core import HttpClient,Settings
        from astrbot_plugin_60s.image_api import ImageService
        from test_http import Response
        calls=[]
        def get(url,**kwargs):
            calls.append((url,kwargs))
            if url.endswith('/v2/60s'):
                return Response(302,headers={'Location':'https://cdn.example/2026-11-08.png'})
            return Response(body=b'opaque-image')
        session=SimpleNamespace(get=get);client=HttpClient(Settings(),session)
        images=ImageService(Settings(),session,lambda:client.fetch('daily'),clock=lambda:self.now)
        service=ManualService(client.fetch,images.query,clock=lambda:self.now)
        for _ in range(2):
            self.assertEqual((await service.query('daily')).image,b'opaque-image')
        self.assertEqual(len(calls),4)
        self.assertEqual([c[1]['params'] for c in calls if 'params' in c[1]],[{'encoding':'image'}]*2)

    async def test_concurrent_commands_are_not_coalesced(self):
        import asyncio
        await asyncio.gather(self.service.query('daily','text'),self.service.query('daily','text'))
        self.assertEqual(self.fetch.await_count,2)

    async def test_timezone_does_not_add_a_manual_date_notice(self):
        self.now=datetime(2026,11,7,17,tzinfo=UTC)
        original='上游旧日原文 2000-01-01\n'
        self.fetch.side_effect=lambda *args:original
        self.assertEqual((await self.service.query('daily','text')).text,original)
