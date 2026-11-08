"""AstrBot commands and opt-in scheduled delivery."""
import asyncio
import base64
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core import ApiError, Engine, HttpClient, Settings, Store, upgrade_config
from .formats import Output, daily_output
from .hot import HotService
from .manual import ManualService
from .image_api import ImageService


class WorkspacePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        updated = upgrade_config(config)
        self.settings = Settings(**{k: updated[k] for k in Settings.__dataclass_fields__ if k in updated})
        # Validate before persisting; AstrBot's writer atomically replaces its file.
        # No API call or delivery is involved in this configuration-only migration.
        if updated != dict(config):
            config.save_config(replace_config=updated)
        self.task = None
        self.session = None
        self.store = None
        self.closed = False
        self.queries = set()
        self.hot = HotService(self._fetch_hot, timeout=self.settings.timeout_seconds)
        self.manual = ManualService(self._fetch_manual, self._load_image,
                                    timezone=self.settings.timezone,
                                    timeout=self.settings.timeout_seconds,
                                    limit_reader=self._manual_limit)

    async def _fetch_manual(self, kind):
        return await self._client().fetch(kind)

    def _manual_limit(self, kind, fmt):
        # Read only explicit server Retry-After limits, never scheduled retry/dedup.
        engine = getattr(self, "engine", None)
        limits = engine.next_fetch if engine is not None else {}
        return max(limits.get("api:" + kind, 0), limits.get("format:" + kind + ":" + fmt, 0))

    def _client(self):
        if self.closed:
            raise ApiError("插件已停用")
        if self.session is None:
            import aiohttp
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.settings.timeout_seconds))
        return HttpClient(self.settings, self.session)

    async def _fetch_hot(self, path):
        return await self._client().fetch_path(path)

    async def _prepare(self, kind, payload, source, text, target):
        return await daily_output(kind, payload, source, text, self.settings.format_for(kind, target), self._load_image)

    async def _load_image(self, expected=None, json_fetch=None):
        client = self._client()
        service = ImageService(self.settings, self.session, json_fetch or (lambda: client.fetch("daily")))
        return await service.query(expected)

    async def initialize(self):
        if self.task is not None or not self.settings.enabled or not self.settings.targets:
            return
        path = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_60s"
        path.mkdir(parents=True, exist_ok=True)
        self.store = Store(path / "deliveries.sqlite3")
        try:
            client = self._client()
            self.engine = Engine(self.settings, self.store, client, self._send, logger.warning, self._prepare, clock=lambda: datetime.now(UTC), image_query=self._load_image)
            self.task = asyncio.create_task(self._run(), name="astrbot-60s-daily")
        except Exception:
            await self.terminate()
            raise

    @staticmethod
    def _components(output):
        from astrbot.api.message_components import Image, Plain
        if isinstance(output, str):
            output = Output(output)
        parts = [Plain(output.text)]
        if output.image is not None:
            parts.append(Image.fromBase64(base64.b64encode(output.image).decode("ascii")))
        return parts

    async def _send(self, target, output):
        if self.context.get_platform_inst(target.split(":", 1)[0]) is None:
            logger.warning("目标 UMO 对应的平台实例不存在或尚未加载，未发送；请检查目标会话")
            return False
        chain = MessageChain()
        chain.chain = self._components(output)
        return await self.context.send_message(target, chain)

    @filter.command("hot")
    async def hot_command(self, event: AstrMessageEvent, source: str = "帮助", argument: str = "", count: str = "", output_format: str = ""):
        """查询热门榜单：hot 榜单 [条数] [text/json/markdown]；网易云详情另需榜单ID。"""
        if self.closed:
            return
        task = asyncio.create_task(self.hot.query(source, argument, count, output_format))
        self.queries.add(task)
        try:
            output = await task
            yield event.chain_result(self._components(output))
        except (ApiError, ValueError) as exc:
            yield event.plain_result(str(exc))
        except Exception:
            logger.exception("热榜查询异常")
            yield event.plain_result("热榜查询失败，请稍后重试或联系管理员检查配置")
        finally:
            self.queries.discard(task)

    @filter.command("60s")
    async def daily_command(self, event: AstrMessageEvent, output_format: str = "image"):
        """手动拉取日报一次：60s [image/text/json/markdown]，仅回复当前会话。"""
        async for result in self._manual_command(event, "daily", output_format):
            yield result

    @filter.command("history")
    async def history_command(self, event: AstrMessageEvent, output_format: str = "text"):
        """手动拉取历史上的今天一次：history [text/json/markdown]，仅回复当前会话。"""
        async for result in self._manual_command(event, "history", output_format):
            yield result

    async def _manual_command(self, event, kind, output_format):
        if self.closed:
            return
        task = asyncio.create_task(self.manual.query(kind, output_format))
        self.queries.add(task)
        try:
            output = await task
            yield event.chain_result(self._components(output))
        except (ApiError, ValueError) as exc:
            yield event.plain_result(str(exc))
        except TimeoutError:
            yield event.plain_result("本次查询超时，已停止；没有自动重试，请稍后手动查询")
        except Exception:
            logger.exception("手动日更查询失败")
            yield event.plain_result("本次查询失败，没有自动重试，请稍后手动查询")
        finally:
            self.queries.discard(task)

    async def _run(self):
        while True:
            try:
                await self.engine.tick(datetime.now(UTC))
            except Exception:
                logger.exception("60s 调度停止，请检查存储和配置后重载插件")
                return
            await asyncio.sleep(30)

    async def terminate(self):
        self.closed = True
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        queries = list(self.queries)
        for task in queries:
            task.cancel()
        await asyncio.gather(*queries, return_exceptions=True)
        self.queries.clear()
        if self.session is not None:
            await self.session.close()
            self.session = None
        if self.store is not None:
            self.store.close()
            self.store = None
