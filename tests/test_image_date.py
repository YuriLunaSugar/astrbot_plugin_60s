import bootstrap
import unittest
from datetime import UTC,date,datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from astrbot_plugin_60s.core import Settings,ApiError
from astrbot_plugin_60s.image_api import ImageService,filename_date
from test_http import Response
from test_core import daily

NOW=datetime(2026,11,8,1,tzinfo=UTC)
TODAY=date(2026,11,8)
class ImageDateTests(unittest.IsolatedAsyncioTestCase):
    def service(self,responses,json_fetch=None,base='https://60s.viki.moe'):
        self.session=SimpleNamespace(get=Mock(side_effect=responses))
        self.json_fetch=json_fetch or AsyncMock(return_value=daily())
        return ImageService(Settings(base_url=base),self.session,self.json_fetch,clock=lambda:NOW)
    def test_only_exact_calendar_filename(self):
        self.assertEqual(filename_date('https://cdn.example/images/2026-11-08.png?date=1900-01-01'),str(TODAY))
        for url in ('https://cdn.example/640?date=2026-11-08','https://cdn.example/2026-11-08/640','https://cdn.example/2026-02-30.png','https://cdn.example/2026-11-08-2026-11-07.png','https://cdn.example/2026-11-08.png.evil','https://cdn.example/no.png#2026-11-08'):
            self.assertIsNone(filename_date(url))
    async def test_today_redirect_one_api_no_json(self):
        service=self.service([Response(302,headers={'Location':'https://cdn.example/2026-11-08.png'}),Response(body=b'opaque')])
        result=await service.query(expected=TODAY)
        self.assertEqual(result.source,str(TODAY));self.assertEqual(result.output.image,b'opaque')
        self.json_fetch.assert_not_awaited()
        self.assertEqual(self.session.get.call_args_list[0].kwargs,{'params':{'encoding':'image'},'allow_redirects':False})
        self.assertTrue(self.session.get.call_args_list[1].kwargs['allow_redirects'])
    async def test_scheduled_old_future_dates_do_not_download(self):
        for day in ('2026-11-07','2026-11-09'):
            service=self.service([Response(302,headers={'Location':f'https://cdn.example/{day}.png'})])
            with self.assertRaises(ApiError):await service.query(expected=TODAY)
            self.assertEqual(self.session.get.call_count,1);self.json_fetch.assert_not_awaited()
    async def test_manual_old_date_is_shown(self):
        service=self.service([Response(302,headers={'Location':'https://cdn.example/2026-11-07.png'}),Response(body=b'old')])
        result=await service.query()
        self.assertEqual(result.source,'2026-11-07');self.assertEqual(result.output.text,'')
    async def test_missing_or_invalid_filename_falls_back_to_json(self):
        for path in ('640?date=1900-01-01','2026-02-30.png'):
            service=self.service([Response(302,headers={'Location':'https://cdn.example/'+path}),Response(body=b'raw')])
            result=await service.query(expected=TODAY)
            self.json_fetch.assert_awaited_once_with();self.assertEqual(result.source,str(TODAY))
            self.assertEqual(result.date_basis,'JSON 回退');self.assertEqual(result.output.text,'')
    async def test_direct_image_self_hosted_json_fallback(self):
        service=self.service([Response(body=b'intranet')],base='http://127.0.0.1:8080/service')
        result=await service.query(expected=TODAY)
        self.assertEqual(result.output.image,b'intranet');self.assertEqual(self.session.get.call_count,1)
        self.assertEqual(self.session.get.call_args.args[0],'http://127.0.0.1:8080/service/v2/60s')
        self.json_fetch.assert_awaited_once_with()
    async def test_unknown_json_manual_labels_unknown_schedule_rejects(self):
        for expected in (None,TODAY):
            service=self.service([Response(body=b'image')],AsyncMock(return_value={'code':200,'data':{}}))
            if expected:
                with self.assertRaises(ApiError):await service.query(expected=expected)
            else:
                result=await service.query();self.assertIsNone(result.source)
                self.assertEqual(result.output.text,'')
    async def test_http_errors_and_missing_location_no_fallback(self):
        for response in (Response(503),Response(302),Response(429,headers={'Retry-After':'60'})):
            service=self.service([response])
            with self.assertRaises(ApiError):await service.query()
            self.json_fetch.assert_not_awaited();self.assertEqual(self.session.get.call_count,1)

    async def test_json_fallback_old_or_future_schedule_does_not_download(self):
        for day in ('2026-11-07','2026-11-09','invalid'):
            service=self.service([Response(302,headers={'Location':'https://cdn.example/640'})],AsyncMock(return_value=daily(day)))
            with self.assertRaises(ApiError):await service.query(expected=TODAY)
            self.assertEqual(self.session.get.call_count,1)
            self.json_fetch.assert_awaited_once_with()

    async def test_relative_location_and_final_redirect_date(self):
        final=Response(body=b'image');final.url='https://cdn.example/2026-11-07.png'
        service=self.service([Response(302,headers={'Location':'/images/2026-11-08.png'}),final])
        with self.assertRaises(ApiError):await service.query(expected=TODAY)
        self.assertEqual(self.session.get.call_args.args[0],'https://60s.viki.moe/images/2026-11-08.png')

    async def test_fallback_http_failure_and_download_failure_no_retry(self):
        service=self.service([Response(302,headers={'Location':'https://cdn.example/640'})],AsyncMock(side_effect=ApiError('HTTP 503')))
        with self.assertRaises(ApiError):await service.query()
        self.assertEqual(self.session.get.call_count,1)
        service=self.service([Response(302,headers={'Location':'https://cdn.example/2026-11-08.png'}),Response(503)])
        with self.assertRaises(ApiError):await service.query()
        self.assertEqual(self.session.get.call_count,2)

    async def test_legacy_config_retained_hidden_and_unused(self):
        import json
        from pathlib import Path
        from astrbot_plugin_60s.core import upgrade_config
        root=Path(__file__).resolve().parents[1]
        self.assertTrue(json.loads((root/'_conf_schema.json').read_text())['image_hosts']['invisible'])
        for hosts in ([],['mmbiz.qpic.cn'],['custom.example']):
            self.assertEqual(upgrade_config({'image_hosts':hosts})['image_hosts'],hosts)
            service=self.service([Response(302,headers={'Location':'http://192.168.1.5/2026-11-08.png'}),Response(body=b'image')])
            service.settings.image_hosts=hosts
            self.assertEqual((await service.query()).output.image,b'image')

    async def test_scheduler_shares_image_and_json_with_text_and_deduplicates(self):
        import tempfile
        from pathlib import Path
        from astrbot_plugin_60s.core import Engine,Store
        from astrbot_plugin_60s.formats import Output
        from test_destinations import A,B
        from test_target_schedule import entry
        entries=[entry(A),entry(B),entry('demo:GroupMessage:C')]
        for item in entries[:2]:item['daily']='image'
        settings=Settings(enabled=True,destinations=entries)
        client=SimpleNamespace(fetch=AsyncMock(return_value=daily()))
        session=SimpleNamespace(get=Mock(side_effect=[Response(302,headers={'Location':'http://192.168.1.5/640'}),Response(body=b'image')]))
        calls=[]
        async def query(day,json_fetch):
            calls.append(day)
            return await ImageService(settings,session,json_fetch,clock=lambda:NOW).query(day)
        async def prepare(kind,payload,source,text,target):
            return Output('上游原始文本')
        send=AsyncMock(return_value=True)
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db')
            try:
                engine=Engine(settings,store,client,send,lambda _:None,prepare,image_query=query)
                await engine.tick(NOW);await engine.tick(NOW)
                self.assertEqual(calls,[TODAY]);self.assertEqual(session.get.call_count,2)
                client.fetch.assert_awaited_once_with('daily')
                self.assertEqual(send.await_count,3)
            finally:store.close()

    async def test_scheduler_stale_retry_recovery_restart_and_window(self):
        import tempfile
        from pathlib import Path
        from datetime import timedelta
        from astrbot_plugin_60s.core import Engine,Store
        from test_destinations import A,B
        from test_target_schedule import entry
        entries=[entry(A),entry(B)]
        for item in entries:item['daily']='image'
        settings=Settings(enabled=True,destinations=entries)
        current=[NOW];day=['2026-11-07'];requests=[]
        def get(url,**kw):
            requests.append((url,kw))
            return Response(302,headers={'Location':f'https://cdn.example/{day[0]}.png'}) if 'params' in kw else Response(body=b'image')
        client=SimpleNamespace(fetch=AsyncMock())
        async def query(expected,json_fetch):
            return await ImageService(settings,SimpleNamespace(get=get),json_fetch,clock=lambda:current[0]).query(expected)
        send=AsyncMock(return_value=True)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.db';store=Store(path)
            try:
                engine=Engine(settings,store,client,send,lambda _:None,image_query=query)
                await engine.tick(current[0]);send.assert_not_awaited()
                current[0]+=timedelta(minutes=1);await engine.tick(current[0]);self.assertEqual(len(requests),1)
                current[0]+=timedelta(minutes=4);day[0]='2026-11-08'
                await engine.tick(current[0]);self.assertEqual(send.await_count,2)
                store.close();store=Store(path)
                engine=Engine(settings,store,client,send,lambda _:None,image_query=query)
                await engine.tick(current[0]+timedelta(minutes=1));self.assertEqual(send.await_count,2)
                self.assertEqual(len(requests),3);client.fetch.assert_not_awaited()
            finally:store.close()

    async def test_manual_cancellation_closes_response(self):
        import asyncio
        response=Response(302,headers={'Location':'https://cdn.example/640'})
        started=asyncio.Event()
        async def pending():started.set();await asyncio.Event().wait()
        service=self.service([response],pending)
        task=asyncio.create_task(service.query());await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertTrue(response.exited)

    async def test_timezone_and_cross_midnight_date_gate(self):
        for instant,accepted in ((datetime(2026,11,7,17,tzinfo=UTC),True),(datetime(2026,11,8,16,tzinfo=UTC),False)):
            service=self.service([Response(302,headers={'Location':'https://cdn.example/2026-11-08.png'}),Response(body=b'image')])
            service.clock=lambda:instant
            if accepted:
                self.assertEqual((await service.query(expected=TODAY)).source,str(TODAY))
            else:
                with self.assertRaises(ApiError):await service.query(expected=TODAY)
                self.assertEqual(self.session.get.call_count,1)

    async def test_scheduler_image_429_persists_shared_api_wait(self):
        import tempfile
        from pathlib import Path
        from datetime import timedelta
        from astrbot_plugin_60s.core import Engine,Store
        from test_destinations import A,B
        from test_target_schedule import entry
        entries=[entry(A),entry(B)]
        for item in entries:item['daily']='image'
        settings=Settings(enabled=True,destinations=entries)
        query=AsyncMock(side_effect=ApiError('HTTP 429',900));send=AsyncMock()
        client=SimpleNamespace(fetch=AsyncMock())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.db';store=Store(path)
            try:
                engine=Engine(settings,store,client,send,lambda _:None,image_query=query)
                await engine.tick(NOW)
                self.assertEqual(query.await_count,1)
                self.assertEqual(engine.next_fetch['api:daily'],NOW.timestamp()+900)
                store.close();store=Store(path)
                restarted=Engine(settings,store,client,send,lambda _:None,image_query=query)
                await restarted.tick(NOW+timedelta(minutes=5))
                await restarted.tick(NOW+timedelta(hours=2))
                self.assertEqual(query.await_count,1);send.assert_not_awaited()
            finally:store.close()

    async def test_legacy_disabled_flags_stay_disabled(self):
        import tempfile
        from pathlib import Path
        from astrbot_plugin_60s.core import Engine,Store
        settings=Settings(enabled=True,targets=['demo:GroupMessage:A'],daily_enabled=False,history_enabled=False,formats={'daily':'image'})
        query=AsyncMock();client=SimpleNamespace(fetch=AsyncMock())
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db')
            try:
                await Engine(settings,store,client,AsyncMock(),lambda _:None,image_query=query).tick(NOW)
                query.assert_not_awaited();client.fetch.assert_not_awaited()
            finally:store.close()
