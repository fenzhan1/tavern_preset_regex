#!/bin/bash
# 修正线上 regex.json：把 <interactive_input> 的 strip 规则改成 input 阶段、排在 wrapper 之前
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
F=data/tavern_preset_regex/regex.json
cp -n "$F" "$F.bak-$(date +%Y%m%d-%H%M%S)"

.venv/bin/python3 - <<'PY'
import json, shutil
from pathlib import Path

p = Path('data/tavern_preset_regex/regex.json')
data = json.loads(p.read_text(encoding='utf-8'))
entries = data if isinstance(data, list) else data.get('regex_entries') or []

def name_of(e):
    return str(e.get('scriptName', ''))

strip_open = strip_close = wrapper = None
for e in entries:
    n = name_of(e)
    if n == '/<interactive_input>/g':
        strip_open = e
    elif n == '/</interactive_input>/g':
        strip_close = e
    elif 'aether opus正则一' in n:
        wrapper = e

if not (strip_open and strip_close and wrapper):
    print('没找到全部三条规则，放弃修改')
    raise SystemExit(0)

print('修改前：')
for e in (strip_open, strip_close, wrapper):
    print('  %-24s markdownOnly=%s promptOnly=%s placement=%s find=%r' % (
        name_of(e), e.get('markdownOnly'), e.get('promptOnly'),
        e.get('placement'), e.get('findRegex')))

# strip 规则改成 input 阶段，并把换行一起吃掉
strip_open['markdownOnly'] = False
strip_open['promptOnly'] = True
strip_open['placement'] = [1]
strip_open['findRegex'] = r'/<interactive_input>\n?/g'

strip_close['markdownOnly'] = False
strip_close['promptOnly'] = True
strip_close['placement'] = [1]
strip_close['findRegex'] = r'/\n?</interactive_input>/g'

# 顺序：strip 两条挪到 wrapper 之前
others = [e for e in entries if e not in (strip_open, strip_close)]
new_entries = []
for e in others:
    if 'aether opus正则一' in name_of(e):
        new_entries.extend([strip_open, strip_close])
    new_entries.append(e)
if len(new_entries) != len(entries):
    new_entries = [strip_open, strip_close] + others

print()
print('修改后：')
for e in (strip_open, strip_close, wrapper):
    print('  %-24s markdownOnly=%s promptOnly=%s placement=%s find=%r' % (
        name_of(e), e.get('markdownOnly'), e.get('promptOnly'),
        e.get('placement'), e.get('findRegex')))

if isinstance(data, list):
    data = new_entries
else:
    key = 'regex_entries' if 'regex_entries' in data else 'entries'
    data[key] = new_entries
p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print()
print('已写入，共 %d 条规则' % len(new_entries))
PY
