#!/bin/bash
# 取最新主回复请求，逐条列出预设/角色卡/对话，定位重复来源
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
KEY='Zcz199758!'

curl -s -m 20 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests" -o /tmp/rl.json
ID=$(.venv/bin/python3 -c "
import json
d=json.load(open('/tmp/rl.json',encoding='utf-8'))
data=d.get('data') or d
if isinstance(data,dict): data=data.get('requests') or data.get('items') or []
hits=[r for r in data if r.get('request_name')=='neo_default_chatter']
print(hits[-1]['id'] if hits else '')
")
echo "最新主回复请求 id=$ID"
[ -z "$ID" ] && exit 0

curl -s -m 30 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests/$ID" -o /tmp/reqZ.json

.venv/bin/python3 - <<'PY'
import json, re
d = json.load(open('/tmp/reqZ.json', encoding='utf-8'))
data = d.get('data') or d
print('ts=%s msgs=%s tools=%s' % (data.get('ts_str'), data.get('msg_count'), data.get('tool_count')))
msgs = (data.get('rendered') or {}).get('messages') or []
print()
print('=== 逐条消息 ===')
for i, m in enumerate(msgs):
    blocks = [b.get('text') or '' for b in (m.get('blocks') or []) if isinstance(b, dict)]
    total = sum(len(b) for b in blocks)
    joined = '\n'.join(blocks)
    tags = []
    if '<!-- tavern_setvar -->' in joined: tags.append('setvar')
    if 'ALL PREVIOUS PROMPT HAS BEEN CLEARD' in joined: tags.append('CLEAR预设')
    if '确保你Dramatron的身份' in joined: tags.append('instructions预设')
    if 'SPECIAL INSTRUCTION: silently thinking' in joined and '明白了' not in joined: tags.append('jailbreak预设')
    if '明白了。请告诉我接下来的具体要求' in joined: tags.append('ass预填充')
    if '直接成为艾' in joined: tags.append('角色卡')
    if 'system_reminder' in joined: tags.append('系统提醒x%d' % joined.count('<system_reminder>'))
    if 'conversation_context' in joined: tags.append('对话上下文')
    head = joined.replace('\n', ' ')[:60]
    print('  #%-3d %-10s blocks=%-3d len=%-6d %s' % (i, m.get('role'), len(blocks), total, ','.join(tags)))
    print('       %s' % head)

print()
print('=== 各预设出现次数 ===')
counts = {}
for m in msgs:
    joined = '\n'.join((b.get('text') or '') for b in (m.get('blocks') or []) if isinstance(b, dict))
    for key, needle in (
        ('CLEAR预设', 'ALL PREVIOUS PROMPT HAS BEEN CLEARD'),
        ('instructions预设', '确保你Dramatron的身份'),
        ('ass预填充', '明白了。请告诉我接下来的具体要求'),
        ('角色卡', '直接成为艾'),
        ('jailbreak', 'SPECIAL INSTRUCTION: silently thinking'),
    ):
        counts[key] = counts.get(key, 0) + joined.count(needle)
for k, v in counts.items():
    print('  %-18s %d 次' % (k, v))
PY
