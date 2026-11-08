import bootstrap  # Expose the repository root as the plugin package.
import asyncio
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from astrbot_plugin_60s.core import ApiError, Engine, Settings, Store, render, retry_delay


def daily(day="2026-11-08"):
    return {"code": 200, "data": {"date": day, "news": ["测试资讯"]}}


def history(items=None):
    return {"code": 200, "data": {"date": "11-8", "items": items if items is not None else [{"year": None, "title": "测试事件", "description": None}]}}


class CoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite3"
        self.store = Store(self.path)
        self.settings = Settings(enabled=True, targets=["test:GroupMessage:1", "test:GroupMessage:2"])
        self.client = AsyncMock()
        self.client.fetch.side_effect = lambda kind: daily() if kind == "daily" else history()
        self.send = AsyncMock()
        self.now = datetime(2026, 11, 8, 1, 0, tzinfo=UTC)
        self.engine = Engine(self.settings, self.store, self.client, self.send, lambda _: None)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_shared_fetch_dedup_restart(self):
        await self.engine.tick(self.now)
        await self.engine.tick(self.now)
        self.assertEqual(self.client.fetch.await_count, 2)
        self.assertEqual(self.send.await_count, 4)
        other = Store(self.path)
        try:
            restarted = Engine(self.settings, other, self.client, self.send, print)
            await restarted.tick(self.now)
            self.assertEqual(self.send.await_count, 4)
        finally:
            other.close()

    async def test_disabled_and_empty_targets(self):
        self.settings.enabled = False
        await self.engine.tick(self.now)
        self.settings.enabled = True
        self.settings.targets = ()
        await self.engine.tick(self.now)
        self.client.fetch.assert_not_awaited()

    async def test_window_and_timezone(self):
        self.assertIsNone(self.settings.due_day(self.now - timedelta(seconds=1)))
        self.assertEqual(self.settings.due_day(self.now), date(2026, 11, 8))
        self.assertEqual(self.settings.due_day(self.now + timedelta(minutes=119)), date(2026, 11, 8))
        self.assertIsNone(self.settings.due_day(self.now + timedelta(hours=2)))
        await self.engine.tick(self.now + timedelta(hours=23))
        self.send.assert_not_awaited()

    async def test_stale_then_recovers_with_cooldown(self):
        self.settings.history_enabled = False
        self.client.fetch.side_effect = [daily("2026-11-07"), daily()]
        await self.engine.tick(self.now)
        await self.engine.tick(self.now + timedelta(minutes=5))
        self.assertEqual(self.client.fetch.await_count, 1)
        self.send.assert_not_awaited()
        await self.engine.tick(self.now + timedelta(minutes=15))
        self.assertEqual(self.send.await_count, 2)

    async def test_rate_limit_each_interface_cooldown(self):
        self.client.fetch.side_effect = ApiError("429", 3600)
        await self.engine.tick(self.now)
        await self.engine.tick(self.now + timedelta(minutes=30))
        self.assertEqual(self.client.fetch.await_count, 2)
        self.send.assert_not_awaited()

    async def test_timeout_and_http_failure(self):
        for exc in (TimeoutError(), ApiError("HTTP 503"), ValueError("bad json")):
            self.engine.next_fetch = {}
            self.client.fetch.side_effect = exc
            await self.engine.tick(self.now)
        self.send.assert_not_awaited()

    async def test_send_failure_does_not_repeat_or_block_other_target(self):
        self.settings.history_enabled = False
        self.send.side_effect = [TimeoutError(), True]
        await self.engine.tick(self.now)
        await self.engine.tick(self.now + timedelta(minutes=1))
        self.assertEqual(self.send.await_count, 2)
        statuses = self.store.db.execute("SELECT status FROM deliveries ORDER BY target").fetchall()
        self.assertEqual(statuses, [("failed_or_unknown",), ("sent",)])

    async def test_concurrent_ticks(self):
        await asyncio.gather(self.engine.tick(self.now), self.engine.tick(self.now))
        self.assertEqual(self.send.await_count, 4)

    async def test_cancellation_retains_claim(self):
        self.settings.history_enabled = False
        self.send.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.engine.tick(self.now)
        self.assertTrue(self.store.has(date(2026, 11, 8), self.settings.targets[0], "daily"))

    def test_nullable_history_and_empty_content(self):
        _, text = render("history", history([None, {}, {"title": "事件", "year": None}]), date(2026, 11, 8))
        self.assertIn("年份不详", text)
        self.assertNotIn("None", text)
        for payload in (history([]), {"code": 200, "data": {"date": "11-8", "items": None}}):
            with self.assertRaises(ApiError):
                render("history", payload, date(2026, 11, 8))

    def test_invalid_config_and_dates(self):
        for values in ({"send_time": "24:00"}, {"base_url": "ftp://foo"}, {"targets": ["123"]}, {"timeout_seconds": 0}):
            with self.assertRaises(ValueError):
                Settings(**values)
        for payload in (daily("bad"), daily("2026-11-09"), {"code": 500}):
            with self.assertRaises(ApiError):
                render("daily", payload, date(2026, 11, 8))

    def test_retry_after(self):
        self.assertEqual(retry_delay("1800"), 1800)
        self.assertEqual(retry_delay("bad"), 900)
        self.assertEqual(retry_delay("Sun, 08 Nov 2026 02:00:00 GMT", self.now), 3600)

    def test_dst_repeated_hour_has_same_daily_key(self):
        s = Settings(timezone="America/New_York", send_time="01:30")
        first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
        second = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
        self.assertEqual(s.due_day(first), s.due_day(second))


    async def test_stale_daily_retries_history_on_time_and_recovers_once(self):
        attempts = 0
        def fetch(kind):
            nonlocal attempts
            if kind == "history":
                return history()
            attempts += 1
            return daily("2026-11-07" if attempts < 3 else "2026-11-08")
        self.client.fetch.side_effect = fetch
        await self.engine.tick(self.now)
        self.assertEqual(self.send.await_count, 2)
        self.assertTrue(all("历史上的今天" in c.args[1] for c in self.send.await_args_list))
        for minutes in (1, 15, 16, 30, 31, 119):
            await self.engine.tick(self.now + timedelta(minutes=minutes))
        self.assertEqual(attempts, 3)
        self.assertEqual(self.client.fetch.await_count, 4)
        self.assertEqual(self.send.await_count, 4)
        self.assertNotIn("daily", self.engine.last_error)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM deliveries WHERE status='sent'").fetchone()[0], 4)

    async def test_daily_429_preserves_other_interface_and_cross_day_deadline(self):
        def limited(kind):
            if kind == "daily":
                raise ApiError("HTTP 429", 27 * 3600)
            return history()
        self.client.fetch.side_effect = limited
        await self.engine.tick(self.now)
        self.assertEqual(self.send.await_count, 2)
        self.assertIn("daily", self.engine.last_error)
        next_day = history()
        next_day["data"]["date"] = "11-9"
        self.client.fetch.side_effect = lambda kind: daily("2026-11-09") if kind == "daily" else next_day
        await self.engine.tick(self.now + timedelta(days=1))
        self.assertEqual(self.send.await_count, 4)
        self.assertEqual([c.args[0] for c in self.client.fetch.await_args_list].count("daily"), 1)
        await self.engine.tick(self.now + timedelta(days=1, hours=3))
        self.assertEqual(self.send.await_count, 4)

    async def test_history_429_does_not_block_daily(self):
        def limited(kind):
            if kind == "history":
                raise ApiError("HTTP 429", 3600)
            return daily()
        self.client.fetch.side_effect = limited
        await self.engine.tick(self.now)
        self.assertEqual(self.send.await_count, 2)
        self.assertTrue(all("每天60秒" in c.args[1] for c in self.send.await_args_list))
        self.client.fetch.side_effect = lambda kind: history()
        await self.engine.tick(self.now + timedelta(minutes=59))
        self.assertEqual(self.client.fetch.await_count, 2)
        await self.engine.tick(self.now + timedelta(hours=1))
        self.assertEqual(self.send.await_count, 4)
        self.assertNotIn("history", self.engine.last_error)

    async def test_new_day_and_exclusive_window_boundary(self):
        self.client.fetch.side_effect = lambda kind: daily("2026-11-07") if kind == "daily" else history()
        await self.engine.tick(self.now)
        self.assertEqual(self.send.await_count, 2)
        self.client.fetch.side_effect = lambda kind: daily() if kind == "daily" else history()
        await self.engine.tick(self.now + timedelta(hours=2))
        self.assertEqual(self.client.fetch.await_count, 2)
        next_history = history()
        next_history["data"]["date"] = "11-9"
        self.client.fetch.side_effect = lambda kind: daily("2026-11-09") if kind == "daily" else next_history
        await self.engine.tick(self.now + timedelta(days=1))
        await self.engine.tick(self.now + timedelta(days=1, minutes=1))
        self.assertEqual(self.send.await_count, 6)
        self.assertEqual(self.client.fetch.await_count, 4)

    async def test_slow_daily_does_not_delay_history(self):
        daily_started = asyncio.Event()
        release_daily = asyncio.Event()
        history_sent = asyncio.Event()
        async def fetch(kind):
            if kind == "daily":
                daily_started.set()
                await release_daily.wait()
                return daily()
            return history()
        async def send(target, text):
            if "历史上的今天" in text:
                history_sent.set()
        self.client.fetch.side_effect = fetch
        self.send.side_effect = send
        task = asyncio.create_task(self.engine.tick(self.now))
        try:
            await asyncio.wait_for(daily_started.wait(), 1)
            await asyncio.wait_for(history_sent.wait(), 1)
        finally:
            release_daily.set()
            await task
        self.assertEqual(self.send.await_count, 4)
