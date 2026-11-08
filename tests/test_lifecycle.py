import bootstrap  # Expose the repository root as the plugin package.
"""Adapter contract tests with fake AstrBot/aiohttp, not real integration tests."""
import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_reload_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            modules = {n: types.ModuleType(n) for n in (
                'astrbot', 'astrbot.api', 'astrbot.api.event', 'astrbot.api.star',
                'astrbot.api.message_components', 'astrbot.core', 'astrbot.core.utils', 'astrbot.core.utils.astrbot_path', 'aiohttp')}
            class Star:
                def __init__(self, context):
                    self.context = context
            modules['astrbot.api'].AstrBotConfig = dict
            modules['astrbot.api'].logger = types.SimpleNamespace(warning=lambda _: None, exception=lambda _: None)
            modules['astrbot.api.event'].MessageChain = types.SimpleNamespace
            modules['astrbot.api.message_components'].Plain = lambda text: ('text', text)
            modules['astrbot.api.message_components'].Image = types.SimpleNamespace(fromBase64=lambda value: ('image', value))
            modules['astrbot.api.event'].AstrMessageEvent = object
            registered_commands = []
            def command(name):
                registered_commands.append(name)
                return lambda function: function
            modules['astrbot.api.event'].filter = types.SimpleNamespace(command=command)
            modules['astrbot.api.star'].Star = Star
            modules['astrbot.api.star'].Context = object
            modules['astrbot.core.utils.astrbot_path'].get_astrbot_data_path = lambda: directory
            session = types.SimpleNamespace(close=AsyncMock())
            modules['aiohttp'].ClientSession = lambda **kw: session
            modules['aiohttp'].ClientTimeout = lambda **kw: None
            with patch.dict(sys.modules, modules):
                module = importlib.import_module('astrbot_plugin_60s.main')
                self.assertEqual(registered_commands, ['hot'])
                class SavedConfig(dict):
                    def save_config(self, replace_config):
                        import json
                        from pathlib import Path
                        Path(directory, 'config.json').write_text(json.dumps(replace_config))
                        self.update(replace_config)
                old_config = SavedConfig(target_formats={}, formats={'history':'json'})
                migrated = module.WorkspacePlugin(object(), old_config)
                import json
                from pathlib import Path
                saved = json.loads(Path(directory, 'config.json').read_text())
                self.assertEqual(saved['target_formats'], '{}')
                self.assertEqual(saved['formats'], {'history':'json'})
                await migrated.terminate()
                invalid = SavedConfig(target_formats={'unapproved:GroupMessage:demo': {'daily':'image'}})
                with self.assertRaises(ValueError):
                    module.WorkspacePlugin(object(), invalid)
                self.assertEqual(json.loads(Path(directory, 'config.json').read_text()), saved)
                disabled = module.WorkspacePlugin(object(), SavedConfig())
                await disabled.initialize()
                self.assertIsNone(disabled.task)
                event = types.SimpleNamespace(chain_result=lambda parts: parts, plain_result=lambda text: text)
                replies = [item async for item in disabled.hot_command(event)]
                self.assertIn('热榜用法', replies[0][0][1])
                self.assertIsNone(disabled.session)
                disabled.hot.fetch = AsyncMock(return_value={'code':200, 'data':[{'title':'模拟热点','hot_value':123}]})
                replies = [item async for item in disabled.hot_command(event, '微博', '3')]
                self.assertIn('模拟热点', replies[0][0][1])
                replies = [item async for item in disabled.hot_command(event, 'weibo', '10', 'markdown')]
                self.assertTrue(replies[0][0][1].startswith('## '))
                replies = [item async for item in disabled.hot_command(event, '微博', '3', 'image')]
                self.assertIn('不支持 image', replies[0])
                replies = [item async for item in disabled.hot_command(event, '未知')]
                self.assertIn('未知榜单', replies[0])
                from astrbot_plugin_60s.formats import Output
                parts = disabled._components(Output('来源日期', b'image-data'))
                self.assertEqual(parts[1][0], 'image')
                self.assertNotIn('image-data', parts[0][1])
                unknown_context = types.SimpleNamespace(get_platform_inst=lambda _: None, send_message=AsyncMock())
                disabled.context = unknown_context
                self.assertFalse(await disabled._send('unknown:GroupMessage:demo', Output('test')))
                unknown_context.send_message.assert_not_awaited()
                await disabled.terminate()
                active = module.WorkspacePlugin(object(), SavedConfig(enabled=True, targets=['test:GroupMessage:1']))
                await active.initialize()
                task = active.task
                await active.initialize()
                self.assertIs(active.task, task)
                await active.terminate()
                self.assertTrue(task.done())
                self.assertIsNone(active.store)
                session.close.assert_awaited_once()
                await active.terminate()
            sys.modules.pop('astrbot_plugin_60s.main', None)
