import bootstrap
import asyncio
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from astrbot_plugin_60s.core import ApiError, Engine, Settings, Store
from test_core import daily, history


class RetryScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.db'
        self.store = Store(self.path)
        self.settings = Settings(
            enabled=True, targets=['test:GroupMessage:1', 'test:GroupMessage:2'],
            schedules={
                'daily': {'send_time':'09:00', 'retry_minutes':5, 'end_time':'10:00'},
                'history': {'send_time':'09:10', 'retry_minutes':2, 'end_time':'09:30'},
            },
        )
        self.now = datetime(2026, 11, 8, 1, tzinfo=UTC)
        self.client = AsyncMock()
        self.client.fetch.side_effect = lambda k: daily() if k=='daily' else history()
        self.send = AsyncMock(return_value=True)
        self.engine = Engine(self.settings,self.store,self.client,self.send,lambda _:None)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_stale_retries_on_configured_interval(self):
        calls=0
        def fetch(kind):
            nonlocal calls
            if kind=='history': return history()
            calls+=1
            return daily('2026-11-07') if calls<3 else daily()
        self.client.fetch.side_effect=fetch
        for minutes in (0,1,4,5,9,10,11):
            await self.engine.tick(self.now+timedelta(minutes=minutes))
        self.assertEqual(calls,3)
        self.assertEqual(self.send.await_count,4)

    async def test_explicit_false_retries_only_failed_target_and_refetches(self):
        self.settings.history_enabled=False
        self.send.side_effect=[False,True,True]
        await self.engine.tick(self.now)
        await self.engine.tick(self.now+timedelta(minutes=4))
        self.assertEqual(self.client.fetch.await_count,1)
        await self.engine.tick(self.now+timedelta(minutes=5))
        await self.engine.tick(self.now+timedelta(minutes=10))
        self.assertEqual(self.client.fetch.await_count,2)
        self.assertEqual([c.args[0] for c in self.send.await_args_list],['test:GroupMessage:1','test:GroupMessage:2','test:GroupMessage:1'])

    async def test_unknown_exception_not_retried(self):
        self.settings.history_enabled=False
        self.send.side_effect=[TimeoutError(),RuntimeError('unknown transport error')]
        await self.engine.tick(self.now)
        await self.engine.tick(self.now+timedelta(minutes=5))
        self.assertEqual(self.send.await_count,2)
        self.assertEqual(self.client.fetch.await_count,1)

    async def test_restart_preserves_retry_and_success_states(self):
        self.settings.history_enabled=False
        self.send.side_effect=[False,True,True]
        await self.engine.tick(self.now)
        other=Store(self.path)
        try:
            restarted=Engine(self.settings,other,self.client,self.send,lambda _:None)
            await restarted.tick(self.now+timedelta(minutes=4))
            self.assertEqual(self.send.await_count,2)
            await restarted.tick(self.now+timedelta(minutes=5))
            self.assertEqual(self.send.await_count,3)
        finally: other.close()

    async def test_network_failure_and_distinct_end_times(self):
        self.client.fetch.side_effect=OSError('network')
        for minutes in (0,10,30,31,60,61):
            await self.engine.tick(self.now+timedelta(minutes=minutes))
        calls=[c.args[0] for c in self.client.fetch.await_args_list]
        self.assertEqual(calls.count('daily'),4)
        self.assertEqual(calls.count('history'),2)
        self.send.assert_not_awaited()

    async def test_429_deadline_survives_restart(self):
        self.settings.history_enabled=False
        self.client.fetch.side_effect=ApiError('429',1800)
        await self.engine.tick(self.now)
        other=Store(self.path)
        try:
            restarted=Engine(self.settings,other,self.client,self.send,lambda _:None)
            await restarted.tick(self.now+timedelta(minutes=5))
            self.assertEqual(self.client.fetch.await_count,1)
            self.client.fetch.side_effect=lambda _:daily()
            await restarted.tick(self.now+timedelta(minutes=30))
            self.assertEqual(self.send.await_count,2)
        finally: other.close()

    async def test_cross_midnight_start_day_dedup_and_next_day(self):
        self.settings=Settings(enabled=True,targets=['test:GroupMessage:1'],history_enabled=False,
            schedules={'daily':{'send_time':'23:50','end_time':'00:20','retry_minutes':5}})
        self.engine=Engine(self.settings,self.store,self.client,self.send,lambda _:None)
        late=datetime(2026,11,8,16,5,tzinfo=UTC) # Nov 9 00:05 local, Nov 8 schedule
        await self.engine.tick(late)
        await self.engine.tick(late+timedelta(minutes=15))
        self.assertEqual(self.send.await_count,1)
        self.assertTrue(self.store.has('2026-11-08','test:GroupMessage:1','daily'))
        self.client.fetch.side_effect=lambda _:daily('2026-11-09')
        await self.engine.tick(datetime(2026,11,9,15,50,tzinfo=UTC))
        self.assertEqual(self.send.await_count,2)

    async def test_no_send_after_fetch_crosses_end(self):
        self.settings.history_enabled=False
        current=self.now+timedelta(minutes=59)
        async def fetch(_):
            nonlocal current
            current=self.now+timedelta(minutes=60,seconds=1)
            return daily()
        self.client.fetch.side_effect=fetch
        self.engine=Engine(self.settings,self.store,self.client,self.send,lambda _:None,clock=lambda:current)
        await self.engine.tick(current)
        self.send.assert_not_awaited()

    def test_inclusive_end_and_invalid_equal_times(self):
        window=self.settings.window('daily',self.now+timedelta(hours=1))
        self.assertIsNotNone(window)
        self.assertIsNone(self.settings.window('daily',self.now+timedelta(hours=1,microseconds=1)))
        with self.assertRaises(ValueError):
            Settings(schedules={'daily':{'send_time':'09:00','end_time':'09:00'}})

    async def test_legacy_pending_and_failed_unknown_stay_blocked(self):
        self.store.claim('2026-11-08','test:GroupMessage:1','daily','2026-11-08')
        self.store.claim('2026-11-08','test:GroupMessage:2','daily','2026-11-08')
        self.store.finish('2026-11-08','test:GroupMessage:2','daily','failed_or_unknown')
        self.settings.history_enabled=False
        await self.engine.tick(self.now)
        self.send.assert_not_awaited()

    async def test_target_formats_share_fetch_and_preparation(self):
        self.settings.history_enabled=False
        self.settings.targets=('test:GroupMessage:1','test:GroupMessage:2','test:GroupMessage:3')
        self.settings.target_formats={
            'test:GroupMessage:1':{'daily':'image'},
            'test:GroupMessage:2':{'daily':'json'},
            'test:GroupMessage:3':{'daily':'image'},
        }
        async def prepare(kind,payload,source,text,target):
            return self.settings.format_for(kind,target)
        prepare_mock=AsyncMock(side_effect=prepare)
        self.engine=Engine(self.settings,self.store,self.client,self.send,lambda _:None,prepare_mock)
        await self.engine.tick(self.now)
        self.assertEqual(self.client.fetch.await_count,1)
        self.assertEqual(prepare_mock.await_count,2)
        self.assertEqual([c.args[1] for c in self.send.await_args_list],['image','json','image'])

    def test_legacy_format_fallback_and_validation(self):
        s=Settings(targets=['test:GroupMessage:1'],formats={'daily':'image','weibo':'json'})
        self.assertEqual(s.format_for('daily','test:GroupMessage:1'),'image')
        for mapping in ({'history':'image'},{'daily':'bad'},{'weibo':'text'}):
            with self.assertRaises(ValueError):
                Settings(targets=['test:GroupMessage:1'],target_formats={'test:GroupMessage:1':mapping})

    async def test_format_failure_does_not_block_other_target(self):
        self.settings.history_enabled=False
        self.settings.target_formats={'test:GroupMessage:1':{'daily':'image'}}
        async def prepare(kind,payload,source,text,target):
            if self.settings.format_for(kind,target)=='image': raise ApiError('image unavailable')
            return text
        self.engine=Engine(self.settings,self.store,self.client,self.send,lambda _:None,prepare)
        await self.engine.tick(self.now)
        self.assertEqual(self.send.await_count,1)
        self.assertEqual(self.send.await_args.args[0],'test:GroupMessage:2')
        await self.engine.tick(self.now+timedelta(minutes=5))
        self.assertEqual(self.client.fetch.await_count,2)
        self.assertEqual(self.send.await_count,1)

    def test_template_target_formats_dont_mutate_saved_config(self):
        entries=[{'__template_key':'target','target':'test:GroupMessage:1','daily':'image','history':''}]
        s=Settings(targets=['test:GroupMessage:1'],target_formats=entries,formats={'history':'json'})
        self.assertEqual(s.format_for('daily','test:GroupMessage:1'),'image')
        self.assertEqual(s.format_for('history','test:GroupMessage:1'),'json')
        self.assertEqual(entries[0]['history'],'')
        with self.assertRaises(ValueError):
            Settings(targets=['test:GroupMessage:1'],target_formats=entries+entries)

    async def test_old_database_schema_migrates_without_reclassifying_unknown(self):
        path=Path(self.tmp.name)/'old.db'
        db=sqlite3.connect(path)
        db.execute('CREATE TABLE deliveries (day TEXT, target TEXT, kind TEXT, source_date TEXT, status TEXT, PRIMARY KEY(day,target,kind))')
        db.execute('INSERT INTO deliveries VALUES (?,?,?,?,?)',('2026-11-08','test:GroupMessage:1','daily','2026-11-08','failed_or_unknown'))
        db.commit()
        db.close()
        migrated=Store(path)
        try:
            self.assertFalse(migrated.eligible('2026-11-08','test:GroupMessage:1','daily'))
            self.assertEqual(migrated.load_schedule_state(),[])
        finally: migrated.close()

    async def test_both_daily_formats_are_per_target(self):
        self.settings.schedules['history']['send_time']='09:00'
        self.settings.target_formats={
            'test:GroupMessage:1':{'daily':'image','history':'markdown'},
            'test:GroupMessage:2':{'daily':'text','history':'json'},
        }
        async def prepare(kind,payload,source,text,target):
            return kind+':'+self.settings.format_for(kind,target)
        self.engine=Engine(self.settings,self.store,self.client,self.send,lambda _:None,prepare)
        await self.engine.tick(self.now)
        actual={(c.args[0],c.args[1]) for c in self.send.await_args_list}
        self.assertEqual(actual,{
            ('test:GroupMessage:1','daily:image'),('test:GroupMessage:2','daily:text'),
            ('test:GroupMessage:1','history:markdown'),('test:GroupMessage:2','history:json'),
        })
        self.assertEqual(self.client.fetch.await_count,2)

    async def test_unknown_and_success_remain_terminal_after_restart(self):
        self.settings.history_enabled=False
        self.send.side_effect=[TimeoutError(),True]
        await self.engine.tick(self.now)
        other=Store(self.path)
        try:
            restarted=Engine(self.settings,other,self.client,self.send,lambda _:None)
            await restarted.tick(self.now+timedelta(minutes=10))
            self.assertEqual(self.send.await_count,2)
            self.assertEqual(self.client.fetch.await_count,1)
        finally: other.close()
