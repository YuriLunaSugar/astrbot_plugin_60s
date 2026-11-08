"""Optional source-contract check against an external AstrBot v4.29.4 checkout.

Run: python3 tests/check_upstream_config_contract.py /path/to/reference-directory
Reference directory contains astrbot_config.py, TemplateListEditor.vue,
ConfigItemRenderer.vue, AstrBotConfig.vue, config_service.py fetched verbatim from v4.29.4.
Executes actual backend class and JS handlers, NOT a browser/DOM/WebUI test.
No AstrBot process starts, and all writes are in TemporaryDirectory.
"""
import bootstrap
import ast
import asyncio
import copy
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from astrbot_plugin_60s.core import Settings, upgrade_config

reference = Path(sys.argv[1])
root = Path(__file__).resolve().parents[1]
schema = json.loads((root / '_conf_schema.json').read_text())
source = (reference / 'astrbot_config.py').read_text()
class_node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == 'AstrBotConfig')
namespace = dict(globals(), ASTRBOT_CONFIG_PATH='', DEFAULT_CONFIG={},
                 DEFAULT_VALUE_MAP={k: v for k, v in [('bool', False), ('int', 0), ('string', ''), ('text', ''), ('list', []), ('dict', {}), ('object', {}), ('template_list', [])]},
                 DASHBOARD_RESET_PASSWORD_ENV='ASTRBOT_60S_TEST_UNUSED_PASSWORD_RESET', logger=logging.getLogger('contract'))
exec(compile(ast.Module(body=[class_node], type_ignores=[]), str(reference / 'astrbot_config.py'), 'exec'), namespace)
Config = namespace['AstrBotConfig']
# Execute the official Dashboard validator as well as the file loader/writer.
from typing import Any
service_tree = ast.parse((reference / 'config_service.py').read_text())
functions = [n for n in service_tree.body if isinstance(n, ast.FunctionDef) and
             n.name in ('try_cast', '_expect_type', '_validate_template_list', 'validate_config')]
namespace['Any'] = Any
exec(compile(ast.Module(body=functions, type_ignores=[]), str(reference / 'config_service.py'), 'exec'), namespace)
validate_config = namespace['validate_config']
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'plugin.json'
    a = 'example_bot:GroupMessage:demo_group_A'
    b = 'example_bot:FriendMessage:demo_user_B'
    for overrides in ({a: {'daily': 'image', 'history': 'json'}},
                      [{'target':a,'daily':'image','history':'json','__template_key':'target'}],
                      json.dumps({a:{'daily':'image','history':'json'}})):
        original = {'targets': [a,b], 'target_formats': overrides,
                    'send_time': '08:15', 'catchup_minutes': 37,
                    'formats': {'daily': 'markdown', 'history': 'text', 'weibo': 'json'},
                    'schedules': {'history': {'send_time': '10:00', 'end_time': '11:30', 'retry_minutes': 8}}}
        path.write_text(json.dumps(original))
        config = Config(str(path), schema=schema)
        assert config['target_formats'] == overrides  # Not erased before plugin initialization.
        updated = upgrade_config(config)
        Settings(**updated)
        errors, validated = validate_config(copy.deepcopy(updated), schema, False)
        assert not errors, errors
        config.save_config(replace_config=validated)
        reloaded = Config(str(path), schema=schema)
        for key in ('targets', 'send_time', 'catchup_minutes', 'formats'):
            assert reloaded[key] == original[key]
        assert reloaded['schedules']['history'] == original['schedules']['history']
        settings = Settings(**reloaded)
        assert settings.targets == (a,b)
        assert settings.format_for('daily',a) == 'image'
        assert settings.format_for('daily',b) == 'markdown'
        assert settings.retry_seconds('history',a) == 480
        assert reloaded['destinations'][1]['daily_legacy_window_minutes'] == 37
        assert upgrade_config(reloaded) == reloaded
        reloaded['destinations'] = []
        reloaded.save_config()
        emptied = upgrade_config(Config(str(path), schema=schema))
        assert Settings(**emptied).targets == ()
        assert emptied['targets'] == [a,b]
    print('PASS: actual AstrBot loader/validator/save/reload, three legacy formats, full target migration and no resurrection')

vue = (reference / 'TemplateListEditor.vue').read_text()
def function(name):
    start = vue.index('function ' + name + '(')
    end = vue.find('\nfunction ', start + 1)
    return vue[start:end]
# Actual upstream new-entry handlers, with the real plugin schema supplied.
script = "const props = {modelValue: [], templates:" + json.dumps(schema['destinations']['templates']) + "};\n" + '''
const defaultValueMap={}; const expandedEntries={value:{}};
let emitted; const emit=(event,value)=>{emitted=value};
''' + function('buildDefaults') + '\n' + function('addEntry') + "\naddEntry('target'); console.log(JSON.stringify(emitted));"
entries=json.loads(subprocess.check_output(['node','-e',script],text=True))
assert len(entries)==1 and entries[0]['target']==''
assert entries[0]['daily']=='image' and entries[0]['history']=='text'
assert entries[0]['daily_send_time']=='09:00' and entries[0]['history_end_time']=='11:00'
try:
    Settings(destinations=entries)
    raise AssertionError('blank UMO accepted')
except ValueError:
    pass
entries[0]['target']='example_bot:GroupMessage:demo_group_A'
assert not Settings(destinations=entries).enabled
errors,_=validate_config({'destinations':entries},schema,False)
assert not errors,errors
print('PASS: actual TemplateListEditor new entry uses image/text and per-target schedule defaults, validates through Dashboard')

renderer=(reference / 'ConfigItemRenderer.vue').read_text()
parent=(reference / 'AstrBotConfig.vue').read_text()
assert "itemMeta?._special === 'umo'" in renderer and 'v-else-if="itemMeta?.options"' in renderer
assert '!metadata[metadataKey].items[key]?.invisible' in parent
for key in ('targets','target_formats','send_time','catchup_minutes','formats','daily_enabled','history_enabled','schedules','destinations_migrated'):
    assert schema[key]['invisible'] is True
assert schema['destinations']['type']=='template_list'
print('PASS: source routes UMO/dropdowns and excludes old fields; DOM and browser interaction NOT tested')
