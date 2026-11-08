import bootstrap
import copy
import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from astrbot_plugin_60s import core


class ConfigMigrationTests(unittest.TestCase):
    def test_legacy_mapping_becomes_editable_json_without_data_loss(self):
        target = 'example_bot:GroupMessage:demo_group_A'
        original = {'targets': [target], 'send_time': '08:15', 'catchup_minutes': 37,
                    'formats': {'daily': 'image', 'history': 'json', 'weibo': 'markdown'},
                    'target_formats': {target: {'history': 'markdown'}},
                    'schedules': {'history': {'send_time': '10:00', 'end_time': '11:00', 'retry_minutes': 7}}}
        before = copy.deepcopy(original)
        upgrade = core.upgrade_config
        updated = upgrade(original)
        self.assertIsInstance(updated['target_formats'], str)
        self.assertEqual(json.loads(updated['target_formats']), before['target_formats'])
        self.assertEqual(original, before)
        self.assertEqual(updated['formats'], before['formats'])
        settings = core.Settings(**updated)
        self.assertEqual(settings.format_for('daily', target), 'image')
        self.assertEqual(settings.format_for('history', target), 'markdown')
        self.assertEqual(settings.retry_seconds('history'), 420)
        now = datetime(2026, 11, 8, 8, 51, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertIsNotNone(settings.window('daily', now))
        self.assertIsNone(settings.window('history', now))
        self.assertEqual(upgrade(updated), updated)

    def test_template_array_keeps_both_target_formats_and_empty_default(self):
        a = 'example_bot:GroupMessage:demo_group_A'
        b = 'example_bot:FriendMessage:demo_user_B'
        entries = [{'__template_key':'target', 'target':a, 'daily':'image', 'history':'text'},
                   {'target':b, 'daily':'markdown', 'history':'json'}]
        upgrade = core.upgrade_config
        config = upgrade({'targets':[a,b], 'target_formats':entries})
        self.assertIsInstance(config['target_formats'], str)
        self.assertEqual(json.loads(config['target_formats']), entries)
        settings = core.Settings(**config)
        self.assertEqual(settings.format_for('daily', a), 'image')
        self.assertEqual(settings.format_for('daily', b), 'markdown')
        self.assertEqual(settings.format_for('history', b), 'json')
        self.assertEqual(json.loads(upgrade({'target_formats':{}})['target_formats']), {})

    def test_invalid_json_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'target_formats.*JSON'):
            core.Settings(target_formats='[{')

    def test_json_editor_does_not_enable_or_add_targets(self):
        self.assertFalse(core.Settings(target_formats='[]').enabled)
        with self.assertRaisesRegex(ValueError, 'targets'):
            core.Settings(target_formats='[{"target":"example_bot:GroupMessage:demo_group_A"}]')
        with self.assertRaisesRegex(ValueError, 'history.*image'):
            core.Settings(targets=['example_bot:GroupMessage:demo_group_A'], target_formats=
                '[{"target":"example_bot:GroupMessage:demo_group_A","history":"image"}]')
