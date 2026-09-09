#!/bin/bash
# 找到最新的 neo_default_chatter 请求并检查顺序
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
echo "最新 neo_default_chatter 请求 id=$ID"
[ -z "$ID" ] && exit 0

curl -s -m 30 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests/$ID" -o /tmp/reqX.json

.venv/bin/python3 - <<'PY'
import json
d = json.load(open('/tmp/reqX.json', encoding='utf-8'))
data = d.get('data') or d
print('ts=%s model=%s msg_count=%s' % (data.get('ts_str'), data.get('model'), data.get('msg_count')))
msgs = (data.get('rendered') or {}).get('messages') or []
print()
print('=== 消息顺序 ===')
for i, m in enumerate(msgs):
    text = ''
    for b in (m.get('blocks') or []):
        if isinstance(b, dict):
            text += b.get('text') or ''
    tags = []
    if '<!-- tavern_setvar -->' in text: tags.append('setvar')
    if 'conversation_context' in text: tags.append('历史')
    if '明白了。请告诉我' in text: tags.append('预填充')
    if '然后直接开始输出' in text: tags.append('jailbreak')
    if '__SUSPEND__' in text: tags.append('SUSPEND')
    if any('tool_call' in json.dumps(b) for b in (m.get('blocks') or []) if isinstance(b, dict)): tags.append('tool_call')
    body = text.replace('\n', ' ')[:70]
    print('  #%-3d %-10s %-28s %s' % (i, m.get('role'), ','.join(tags), body))

# 判定
joined = '\n'.join(
    ''.join(b.get('text') or '' for b in (m.get('blocks') or []) if isinstance(b, dict))
    for m in msgs
)
print()
checks = {}
try:
    checks['预填充在最后（新输入之后）'] = joined.index('本轮') < joined.index('明白了。请告诉我')
except ValueError:
    # 用「明白了」最后一次出现判断
    last_prefill = joined.rfind('明白了。请告诉我')
    checks['预填充在最后（新输入之后）'] = last_prefill > joined.rfind('__SUSPEND__')
for name, ok in checks.items():
    print('  [%s] %s' % ('OK' if ok else 'FAIL', name))
PY
