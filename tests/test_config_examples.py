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
        targets=self.schema['targets']
        self.assertEqual(targets['default'],[])
        self.assertEqual(targets['_special'],'umo')
        self.assertTrue(targets['obvious_hint'])
        self.assertIn('example_bot:GroupMessage:demo_group_A',targets['hint'])
        self.assertIn('event.unified_msg_origin',targets['hint'])
        formats=self.schema['target_formats']
        self.assertEqual(json.loads(formats['default']),[])
        self.assertIn('\"daily\":\"image\"',formats['hint'])
        self.assertIn('\"history\":\"json\"',formats['hint'])
        self.assertFalse(self.schema['enabled']['default'])

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
