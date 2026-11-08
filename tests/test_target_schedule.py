import bootstrap
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from astrbot_plugin_60s.core import Settings, Engine, Store, upgrade_config, ApiError
from test_core import daily, history
from test_destinations import A,B


def entry(target, **values):
    return dict(target=target,daily='text',history='off',daily_send_time='09:00',
                daily_retry_minutes=5,daily_end_time='10:00',**values)


class TargetScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'state.db'
        self.store=Store(self.path)
        self.now=datetime(2026,11,8,1,tzinfo=UTC)
        self.client=AsyncMock()
        self.client.fetch.side_effect=lambda k: daily() if k=='daily' else history()
        self.send=AsyncMock(return_value=True)
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def engine(self, entries, store=None, prepare=None):
        return Engine(Settings(enabled=True,destinations=entries),store or self.store,
                      self.client,self.send,lambda _:None,prepare)

    async def test_target_retry_times_are_independent_and_shared_fetch(self):
        a=entry(A);b=entry(B);b['daily_retry_minutes']=2
        engine=self.engine([a,b])
        self.send.return_value=False
        await engine.tick(self.now)
        self.assertEqual(self.client.fetch.await_count,1)
        await engine.tick(self.now+timedelta(minutes=2))
        self.assertEqual([c.args[0] for c in self.send.await_args_list],[A,B,B])
        self.send.return_value=True
        await engine.tick(self.now+timedelta(minutes=5))
        await engine.tick(self.now+timedelta(minutes=10))
        self.assertEqual([c.args[0] for c in self.send.await_args_list],[A,B,B,A,B])

    async def test_off_and_distinct_start_end(self):
        a=entry(A);a['daily']='off'
        b=entry(B);b.update(daily_send_time='09:10',daily_end_time='09:12')
        engine=self.engine([a,b]);self.send.return_value=False
        await engine.tick(self.now)
        self.client.fetch.assert_not_awaited()
        await engine.tick(self.now+timedelta(minutes=10))
        await engine.tick(self.now+timedelta(minutes=15))
        self.assertEqual(self.send.await_count,1)
        self.assertEqual(self.send.await_args.args[0],B)
        self.assertEqual(self.client.fetch.await_args.args[0],'daily')

    async def test_restart_unknown_dedup_and_independent_retry(self):
        a=entry(A);b=entry(B);b['daily_retry_minutes']=2
        self.send.side_effect=[TimeoutError(),False,True]
        engine=self.engine([a,b]);await engine.tick(self.now)
        other=Store(self.path)
        try:
            restarted=self.engine([a,b],other)
            await restarted.tick(self.now+timedelta(minutes=1))
            await restarted.tick(self.now+timedelta(minutes=2))
            await restarted.tick(self.now+timedelta(minutes=20))
            self.assertEqual([c.args[0] for c in self.send.await_args_list],[A,B,B])
        finally: other.close()

    async def test_cross_midnight_and_exact_end(self):
        a=entry(A);a.update(daily_send_time='23:50',daily_end_time='00:20')
        engine=self.engine([a]);late=datetime(2026,11,8,16,20,tzinfo=UTC)
        await engine.tick(late)
        self.assertTrue(self.store.has('2026-11-08',A,'daily'))
        await engine.tick(late+timedelta(microseconds=1))
        self.assertEqual(self.send.await_count,1)
        self.client.fetch.side_effect=lambda _:daily('2026-11-09')
        await engine.tick(datetime(2026,11,9,15,50,tzinfo=UTC))
        self.assertEqual(self.send.await_count,2)

    async def test_stale_daily_does_not_block_history_on_another_target(self):
        a=entry(A);b=entry(B);b.update(daily='off',history='text',history_send_time='09:00')
        self.client.fetch.side_effect=lambda k:daily('2026-11-07') if k=='daily' else history()
        engine=self.engine([a,b]);await engine.tick(self.now)
        self.assertEqual([c.args[0] for c in self.send.await_args_list],[B])
        self.client.fetch.side_effect=lambda k:daily() if k=='daily' else history()
        await engine.tick(self.now+timedelta(minutes=5))
        self.assertEqual([c.args[0] for c in self.send.await_args_list],[B,A])

    async def test_429_shared_endpoint_retry_after_survives_restart(self):
        a=entry(A);b=entry(B);b['daily_send_time']='09:05'
        self.client.fetch.side_effect=ApiError('429',1800)
        await self.engine([a,b]).tick(self.now)
        other=Store(self.path)
        try:
            engine=self.engine([a,b],other)
            await engine.tick(self.now+timedelta(minutes=5))
            self.assertEqual(self.client.fetch.await_count,1)
            self.client.fetch.side_effect=lambda _:daily()
            await engine.tick(self.now+timedelta(minutes=30))
            self.assertEqual(self.send.await_count,2)
        finally: other.close()

    def test_migration_copies_schedules_off_and_preserves_legacy_boundary(self):
        new=upgrade_config({'targets':[A,B],'send_time':'08:15','catchup_minutes':37,
                            'daily_enabled':False,'schedules':{'history':{'send_time':'10:00','retry_minutes':3,'end_time':'11:20'}}})
        s=Settings(**new)
        for target in (A,B):
            self.assertEqual(s.format_for('daily',target),'off')
            self.assertEqual(s.retry_seconds('history',target),180)
            self.assertIsNone(s.window('daily',datetime(2026,11,8,0,52,tzinfo=UTC),target))
        self.assertEqual(new['destinations'][0]['history_end_time'],'11:20')

    async def test_two_interfaces_on_same_target_have_distinct_times(self):
        a=entry(A);a.update(history='json',history_send_time='09:02',history_retry_minutes=1,history_end_time='09:04')
        self.send.return_value=False
        engine=self.engine([a])
        await engine.tick(self.now)
        await engine.tick(self.now+timedelta(minutes=2))
        await engine.tick(self.now+timedelta(minutes=3))
        await engine.tick(self.now+timedelta(minutes=5))
        self.assertEqual([c.args[0] for c in self.client.fetch.await_args_list],['daily','history','history','daily'])

    async def test_shared_preparation_and_end_crossed_during_fetch(self):
        a=entry(A);b=entry(B)
        prepare=AsyncMock(return_value='same prepared text')
        await self.engine([a,b],prepare=prepare).tick(self.now)
        self.assertEqual(prepare.await_count,1)
        self.assertEqual(self.send.await_count,2)
        c=entry('example_bot:GroupMessage:demo_C')
        now=self.now
        async def slow_fetch(_):
            nonlocal now
            now += timedelta(hours=2)
            return daily()
        self.client.fetch.side_effect=slow_fetch
        engine=Engine(Settings(enabled=True,destinations=[c]),self.store,self.client,self.send,lambda _:None,clock=lambda:now)
        await engine.tick(self.now)
        self.assertEqual(self.send.await_count,2)

    async def test_old_interface_cooldown_and_old_success_survive_upgrade(self):
        self.store.save_schedule_state('daily',self.now.timestamp()+300,'old failure')
        self.store.claim('2026-11-08',A,'daily','2026-11-08')
        self.store.finish('2026-11-08',A,'daily','sent')
        engine=self.engine([entry(A),entry(B)])
        await engine.tick(self.now)
        self.client.fetch.assert_not_awaited()
        await engine.tick(self.now+timedelta(minutes=5))
        self.assertEqual([c.args[0] for c in self.send.await_args_list],[B])

    async def test_cancellation_releases_shared_fetch_task(self):
        import asyncio
        started=asyncio.Event();cancelled=asyncio.Event()
        async def fetch(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.client.fetch.side_effect=fetch
        engine=self.engine([entry(A),entry(B)])
        tick=asyncio.create_task(engine.tick(self.now))
        await asyncio.wait_for(started.wait(),1)
        tick.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await tick
        self.assertTrue(cancelled.is_set())
        self.assertTrue(all(t.done() for t in engine.round_tasks.values()))
        self.send.assert_not_awaited()

    async def test_image_retry_after_is_shared_without_blocking_text(self):
        a=entry(A);a['daily']='image'
        b=entry(B);b.update(daily='image',daily_send_time='09:05')
        c=entry('example_bot:GroupMessage:demo_C');c['daily_send_time']='09:05'
        image_calls=0
        async def prepare(kind,payload,source,text,target):
            nonlocal image_calls
            if target in (A,B):
                image_calls+=1
                if image_calls==1: raise ApiError('image 429',1800)
            return text
        engine=self.engine([a,b,c],prepare=prepare)
        await engine.tick(self.now)
        await engine.tick(self.now+timedelta(minutes=5))
        self.assertEqual(image_calls,1)
        self.assertEqual([c.args[0] for c in self.send.await_args_list],['example_bot:GroupMessage:demo_C'])
        await engine.tick(self.now+timedelta(minutes=30))
        self.assertEqual(image_calls,2)
        self.assertEqual(self.send.await_count,3)
