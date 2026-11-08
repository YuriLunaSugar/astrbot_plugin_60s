"""Optional source-contract check against an external AstrBot v4.29.4 checkout.

Run: python3 tests/check_upstream_config_contract.py /path/to/reference-directory
Reference directory contains astrbot_config.py, TemplateListEditor.vue,
ConfigItemRenderer.vue, AstrBotConfig.vue fetched verbatim from v4.29.4.
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
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'plugin.json'
    target = 'example_bot:GroupMessage:demo_group_A'
    original = {'targets': [target], 'target_formats': {target: {'daily': 'image', 'history': 'json'}},
                'send_time': '08:15', 'catchup_minutes': 37, 'formats': {'daily': 'markdown', 'history': 'text', 'weibo': 'json'},
                'schedules': {'history': {'send_time': '10:00', 'end_time': '11:30', 'retry_minutes': 8}}}
    path.write_text(json.dumps(original))
    config = Config(str(path), schema=schema)
    # AstrBot retains the mismatched old mapping even when the schema default is [] or text.
    assert isinstance(config['target_formats'], dict)
    updated = upgrade_config(config)
    Settings(**updated)  # Validate before saving, just as plugin initialization does.
    config.save_config(replace_config=updated)
    reloaded = Config(str(path), schema=schema)
    assert json.loads(reloaded['target_formats']) == original['target_formats']
    for key in ('send_time', 'catchup_minutes', 'formats'):
        assert reloaded[key] == original[key]
    assert reloaded['schedules']['history'] == original['schedules']['history']
    assert Settings(**reloaded).format_for('daily', target) == 'image'
    assert upgrade_config(reloaded) == reloaded
    print('PASS: actual AstrBotConfig load/save/reload retains legacy values and new JSON text')

vue = (reference / 'TemplateListEditor.vue').read_text()
def function(name):
    start = vue.index('function ' + name + '(')
    end = vue.find('\nfunction ', start + 1)
    return vue[start:end]
# Use the upstream handlers unchanged, with only their component state supplied.
script = '''const assert = require('node:assert/strict');
const props = {modelValue: {}, templates: {target: {items: {target: {type:'string', default:''}}}}};
const defaultValueMap = {}; const expandedEntries = {value:{}};
let emitted; const emit = (event, value) => { emitted = value; };
''' + function('buildDefaults') + '\n' + function('addEntry') + '''
assert.throws(() => addEntry('target'), /not iterable/);
props.modelValue = []; addEntry('target');
assert.deepEqual(emitted, [{__template_key:'target',target:''}]);
console.log('PASS: actual upstream addEntry rejects legacy mapping, accepts array');
'''
subprocess.run(['node', '-e', script], check=True)
# Source route evidence, explicitly not proof of Vuetify rendering or menu interaction.
renderer = (reference / 'ConfigItemRenderer.vue').read_text()
assert schema['target_formats']['type'] == 'text'
assert "v-else-if=\"itemMeta?.type === 'text'\"" in renderer
assert '<v-textarea' in renderer
parent = (reference / 'AstrBotConfig.vue').read_text()
assert '!metadata[metadataKey].items[key]?.invisible' in parent
for key in ('send_time', 'catchup_minutes', 'formats'):
    assert schema[key]['invisible'] is True
print('PASS: source routes text to textarea and excludes hidden compatibility fields; DOM NOT tested')
