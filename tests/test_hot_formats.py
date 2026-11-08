import bootstrap  # Expose the repository root as the plugin package.
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_60s.core import ApiError
from astrbot_plugin_60s.formats import check_format
from astrbot_plugin_60s.hot import BOARDS, LOOKUP, HotService, parse_query
from test_http import Response


class HotTests(unittest.IsolatedAsyncioTestCase):
    def test_routes_and_arguments(self):
        self.assertEqual(len(BOARDS), 24)
        self.assertEqual(parse_query('weibo', '10')[2], 10)
        self.assertEqual(parse_query('bilibili')[0].key, 'bili')
        self.assertEqual(parse_query('xiaohongshu')[0].key, 'rednote')
        for board in BOARDS:
            args = ('12345', '5') if board.key == 'ncm_detail' else ('5', '')
            b, path, limit = parse_query(board.name, *args)
            self.assertEqual(b, board)
            self.assertEqual(limit, 5)
            self.assertNotIn('{', path)
        self.assertEqual(parse_query('hn', 'best', '3')[1], 'hacker-news/best')
        self.assertEqual(parse_query('IT之家', 'week', '3')[1], 'it-news/rank?type=week')
        for args in [('unknown', '', ''), ('微博', '21', ''), ('网易云详情', '../abc', ''), ('hn','invalid','')]:
            with self.assertRaises(ValueError):
                parse_query(*args)

    async def test_cache_singleflight_keeps_raw_body_and_ignores_local_limit(self):
        original='{ "code":200, "data": [1,2,3] }\n'
        fetch=AsyncMock(return_value=original)
        service=HotService(fetch)
        results=await asyncio.gather(service.query('微博','3','json'),service.query('微博','5','json'))
        self.assertEqual(fetch.await_count,1)
        self.assertEqual([out.text for out in results],[original,original])
        self.assertIn('网易云详情',(await service.query('帮助')).text)

    async def test_limits_timeouts_and_unsupported_image(self):
        fetch = AsyncMock(side_effect=ApiError('429',3600))
        service = HotService(fetch, clock=lambda:100)
        for _ in range(2):
            with self.assertRaises(ApiError):
                await service.query('微博')
        self.assertEqual(fetch.await_count,1)
        service = HotService(fetch)
        with self.assertRaisesRegex(ApiError,'不支持 image'):
            await service.query('微博','image')
        self.assertEqual(fetch.await_count,1)
        for failure in (TimeoutError(),ValueError()):
            service = HotService(AsyncMock(side_effect=failure))
            with self.assertRaises(ApiError):
                await service.query('微博')

    async def test_command_format_suffix_and_conflicts(self):
        clock=[0]
        fetch=AsyncMock(side_effect=lambda path,fmt: '# 上游原文\n' if fmt=='markdown' else '{"code":200,"data":[1]}')
        service=HotService(fetch,clock=lambda:clock[0])
        self.assertEqual((await service.query('微博','markdown')).text,'# 上游原文\n')
        clock[0]=3
        self.assertEqual((await service.query('微博','5','json')).text,'{"code":200,"data":[1]}')
        self.assertEqual(fetch.await_count,2)
        for args in [('微博','json','5',''),('微博','json','markdown',''),('微博','5','yaml','')]:
            with self.assertRaises(ValueError):await service.query(*args)
        with self.assertRaisesRegex(ApiError,'不支持 image'):await service.query('微博','5','image')
        service=HotService(fetch)
        await service.query('hn','best','3','json')
        self.assertEqual(fetch.await_args.args,('hacker-news/best','json'))
