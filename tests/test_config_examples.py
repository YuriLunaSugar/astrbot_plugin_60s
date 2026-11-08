import bootstrap
import json
import unittest
from pathlib import Path

from astrbot_plugin_60s.core import Settings


class ConfigExamplesTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]
        self.schema=json.loads((self.root/'_conf_schema.json').read_text())

    def test_visible_hints_and_safe_defaults(self):
        self.assertTrue(self.schema['targets']['invisible'])
        self.assertTrue(self.schema['target_formats']['invisible'])
        self.assertFalse(self.schema['enabled']['default'])
        dest=self.schema['destinations']
        self.assertEqual(dest['default'], [])
        items=dest['templates']['target']['items']
        self.assertEqual(items['target']['_special'], 'umo')
        self.assertEqual(items['target']['default'], '')
        self.assertEqual(items['daily']['default'], 'image')
        self.assertEqual(items['history']['default'], 'text')
        self.assertNotIn('image',items['history']['options'])
        self.assertIn('event.unified_msg_origin',items['target']['hint'])

    def test_examples_parse_without_enabling_delivery(self):
        a='example_bot:GroupMessage:demo_group_A'
        b='example_bot:FriendMessage:demo_user_B'
        config=Settings(targets=[a,b],target_formats=[
            {'__template_key':'target','target':a,'daily':'image','history':'text'},
            {'__template_key':'target','target':b,'daily':'markdown','history':'json'},
        ])
        self.assertFalse(config.enabled)
        self.assertEqual(config.format_for('daily',a),'image')
        self.assertEqual(config.format_for('history',a),'text')
        self.assertEqual(config.format_for('daily',b),'markdown')
        self.assertEqual(config.format_for('history',b),'json')
        actual=json.loads((self.root/'config.example.json').read_text())
        self.assertEqual(actual['targets'],[])
        self.assertEqual(json.loads(actual['target_formats']),[])
        self.assertFalse(Settings(**actual).enabled)
