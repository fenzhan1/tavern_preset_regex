#!/bin/bash
# 找出 <interactive_input> 的来源：预设条目？角色卡？
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== setvar.json 里含 interactive_input 的条目 ==="
.venv/bin/python3 - <<'PY'
import json
d = json.load(open('data/tavern_preset_regex/setvar.json', encoding='utf-8'))
for i, p in enumerate(d.get('prompts', [])):
    c = str(p.get('content') or '')
    if 'interactive_input' in c:
        print('  #%d %s role=%s' % (i, p.get('name'), p.get('role')))
        for line in c.splitlines():
            if 'interactive_input' in line:
                print('      ', line.strip()[:100])
PY

echo
echo "=== 角色卡 / 其他数据文件里含 interactive_input ==="
grep -rl "interactive_input" data/ 2>/dev/null | head -20

echo
echo "=== 最新请求 #11 的 #3 消息（历史块）开头 600 字 ==="
KEY='Zcz199758!'
curl -s -m 30 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests/11" -o /tmp/r11.json
.venv/bin/python3 - <<'PY'
import json
d = json.load(open('/tmp/r11.json', encoding='utf-8'))
data = d.get('data') or d
msgs = (data.get('rendered') or {}).get('messages') or []
m = msgs[3]
parts = [b.get('text') or '' for b in (m.get('blocks') or []) if isinstance(b, dict)]
print('blocks 数:', len(parts))
for pi, p in enumerate(parts):
    print('--- block %d (len=%d) 前 400 字 ---' % (pi, len(p)))
    print(p[:400])
    print('   open=%d close=%d' % (p.count('<interactive_input>'), p.count('</interactive_input>')))
PY
