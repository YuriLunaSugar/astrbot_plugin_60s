import bootstrap  # Expose the repository root as the plugin package.
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_60s.core import ApiError
from astrbot_plugin_60s.formats import check_format, daily_output
from astrbot_plugin_60s.hot import BOARDS, LOOKUP, HotService, normalize, parse_query
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

    def test_response_shapes(self):
        shapes = [
            ('douyin', {'title':'热点','hot_value':0,'link':'https://example.com'}),
            ('baidu', {'title':'话题','score_desc':'2万','url':'https://example.com'}),
            ('douban_movie', {'title':'电影','rating':8.2,'url':'https://example.com'}),
            ('ncm_list', {'name':'榜名','id':123}),
            ('ncm_detail', {'title':'歌曲','artist':[None,{'name':'歌手'}]}),
            ('maoyan_movie', {'movie_name':'电影','box_office_desc':'12万'}),
            ('maoyan_tv', {'programme_name':'节目','attention_rate_desc':'1%','channel_name':'频道'}),
            ('maoyan_web', {'series_name':'剧','curr_heat_desc':'1234'}),
        ]
        for key, item in shapes:
            data = {'list':[None,{},item], 'updated':'真实日期'} if key.startswith('maoyan') else [None,{},item]
            rows, _ = normalize({'code':200,'data':data}, LOOKUP[key], 5)
            self.assertEqual(len(rows),1)
            self.assertTrue(rows[0]['title'])
        with self.assertRaises(ApiError):
            normalize({'code':200,'data':None}, LOOKUP['weibo'], 5)

    async def test_cache_singleflight_formats_and_limit(self):
        fetch = AsyncMock(return_value={'code':200,'data':[{'title':f'测试{i}','hot_value':i} for i in range(30)]})
        service = HotService(fetch)
        results = await asyncio.gather(service.query('微博','3','json'),service.query('微博','5','json'))
        self.assertEqual(fetch.await_count,1)
        self.assertEqual(len(json.loads(results[0].text)['items']),3)
        self.assertEqual(len(json.loads(results[1].text)['items']),5)
        help_result = await service.query('帮助')
        self.assertIn('网易云详情',help_result.text)
        self.assertEqual(fetch.await_count,1)

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

    async def test_image_output_and_text_encodings(self):
        loader = AsyncMock(return_value=b'valid-image')
        payload = {'data':{'image':'https://mmbiz.qpic.cn/a'}}
        for fmt in ('text','json','markdown'):
            result = await daily_output('daily',payload,'2026-11-08','正文',fmt,loader)
            self.assertIsNone(result.image)
            if fmt == 'json':
                self.assertEqual(json.loads(result.text)['source_date'],'2026-11-08')
        with self.assertRaisesRegex(ApiError,'不支持 image'):
            await daily_output('history',payload,'11-8','正文','image',loader)
    async def test_command_format_suffix_and_conflicts(self):
        fetch=AsyncMock(return_value={'code':200,'data':[{'title':'模拟'}]})
        service=HotService(fetch)
        output=await service.query('微博','markdown')
        self.assertTrue(output.text.startswith('## '))
        output=await service.query('微博','5','json')
        self.assertEqual(len(json.loads(output.text)['items']),1)
        self.assertEqual(fetch.await_count,1)
        for args in [('微博','json','5',''),('微博','json','markdown',''),('微博','5','yaml','')]:
            with self.assertRaises(ValueError):
                await service.query(*args)
        with self.assertRaisesRegex(ApiError,'不支持 image'):
            await service.query('微博','5','image')
        service=HotService(fetch)
        output=await service.query('hn','best','3','json')
        self.assertEqual(json.loads(output.text)['title'],'HackerNews榜单')
        self.assertEqual(fetch.await_args.args[0],'hacker-news/best')
