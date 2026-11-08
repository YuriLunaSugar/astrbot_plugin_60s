import bootstrap
import copy
import json
import unittest
from astrbot_plugin_60s.core import Settings, upgrade_config

A='example_bot:GroupMessage:demo_A'
B='example_bot:FriendMessage:demo_B'


class DestinationTests(unittest.TestCase):
    def test_all_legacy_targets_migrate_with_effective_formats(self):
        overrides={A:{'history':'markdown'}}
        for raw in [overrides, [{'target':A,'history':'markdown','daily':''}], json.dumps(overrides)]:
            old={'targets':[A,B], 'target_formats':raw, 'formats':{'daily':'text','history':'json'},
                 'send_time':'08:25','catchup_minutes':31, 'history_enabled':True,
                 'schedules':{'daily':{'send_time':'08:30','end_time':'10:00','retry_minutes':9}}}
            before=copy.deepcopy(old)
            new=upgrade_config(old)
            self.assertEqual([{k:e[k] for k in ('__template_key','target','daily','history')} for e in new['destinations']], [
                {'__template_key':'target','target':A,'daily':'text','history':'markdown'},
                {'__template_key':'target','target':B,'daily':'text','history':'json'}])
            self.assertTrue(new['destinations_migrated'])
            self.assertEqual(new['schedules'],before['schedules'])
            self.assertEqual(new['send_time'],'08:25')
            self.assertTrue(Settings(**new).history_enabled)
            self.assertEqual(old,before)
            self.assertEqual(upgrade_config(new),new)

    def test_delete_all_stays_empty_after_repeated_upgrades(self):
        config=upgrade_config({'targets':[A]})
        config['destinations']=[]
        for _ in range(3):
            config=upgrade_config(config)
            self.assertEqual(Settings(**config).targets,())
        self.assertEqual(config['targets'],[A])

    def test_new_entries_are_only_target_source_with_image_text_defaults(self):
        settings=Settings(destinations=[{'target':B}],targets=[A],target_formats='invalid legacy')
        self.assertEqual(settings.targets,(B,))
        self.assertEqual(settings.format_for('daily',B),'image')
        self.assertEqual(settings.format_for('history',B),'text')
        self.assertFalse(settings.enabled)

    def test_invalid_duplicate_and_unknown_types(self):
        for entries,error in [([{'target':''}],'UMO'),([{'target':'123'}],'UMO'),
                              ([{'target':'bot:Unknown:demo'}],'UMO'),
                              ([{'target':A},{'target':A}],'重复'),
                              ([{'target':A,'history':'image'}],'history'),
                              ([{'target':A,'__template_key':'unknown'}],'模板')]:
            with self.subTest(entries=entries),self.assertRaisesRegex(ValueError,error):
                Settings(destinations=entries)

    def test_unapproved_legacy_override_is_not_added(self):
        with self.assertRaisesRegex(ValueError,'targets'):
            upgrade_config({'targets':[A],'target_formats':{B:{'daily':'image'}}})

    def test_already_selected_new_targets_are_preserved_during_first_migration(self):
        result=upgrade_config({'targets':[A], 'destinations':[{'target':B}]})
        self.assertEqual(Settings(**result).targets,(A,B))
        self.assertEqual(Settings(**result).format_for('daily',B),'image')
