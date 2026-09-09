#!/bin/bash
# 导出全部正则规则（顺序、启用、target、模式、替换）
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

.venv/bin/python3 - <<'PY'
import json
d = json.load(open('data/tavern_preset_regex/regex.json', encoding='utf-8'))
entries = d if isinstance(d, list) else d.get('regex_entries') or d.get('entries') or []
print('顶层类型:', type(d).__name__, '| 字段:', list(d.keys()) if isinstance(d, dict) else '-')
print('规则数:', len(entries))
print()
for i, e in enumerate(entries):
    if not isinstance(e, dict):
        print(i, '非 dict'); continue
    name = e.get('script_name') or e.get('scriptName') or e.get('name') or ''
    find = str(e.get('find_regex') or e.get('findRegex') or '')
    repl = str(e.get('replace_string') or e.get('replaceString') or '')
    disabled = e.get('disabled')
    target = e.get('target') or e.get('apply_to') or ''
    print('#%d  %s' % (i, name))
    print('   disabled=%s target=%r' % (disabled, target))
    print('   find=%r' % find[:160])
    print('   repl=%r' % repl[:160])
    print()
PY
