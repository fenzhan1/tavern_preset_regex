#!/bin/bash
# 查 regex.json 里包装 <interactive_input> 的规则
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

.venv/bin/python3 - <<'PY'
import json
d = json.load(open('data/tavern_preset_regex/regex.json', encoding='utf-8'))
entries = d if isinstance(d, list) else d.get('regex_entries') or d.get('entries') or []
print('规则总数:', len(entries))
print()
hits = []
for i, e in enumerate(entries):
    if not isinstance(e, dict):
        continue
    find = str(e.get('find_regex') or e.get('findRegex') or '')
    repl = str(e.get('replace_string') or e.get('replaceString') or '')
    name = str(e.get('script_name') or e.get('scriptName') or e.get('name') or '')
    if 'interactive_input' in find or 'interactive_input' in repl:
        hits.append((i, name, e.get('disabled'), find, repl))
print('=== 涉及 interactive_input 的规则：%d 条 ===' % len(hits))
for i, name, disabled, find, repl in hits:
    print('--- #%d %s (disabled=%s) ---' % (i, name, disabled))
    print('  find  :', find[:300])
    print('  replace:', repl[:300])
    print()
PY
