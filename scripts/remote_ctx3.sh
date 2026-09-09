#!/bin/bash
# 找最大的主回复请求，拆解上下文构成
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

curl -s -m 30 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests/$ID" -o /tmp/reqX.json

.venv/bin/python3 - <<'PY'
import json
d = json.load(open('/tmp/reqX.json', encoding='utf-8'))
data = d.get('data') or d
print('ts=%s model=%s msgs=%s tools=%s' % (
    data.get('ts_str'), data.get('model'), data.get('msg_count'), data.get('tool_count')))

msgs = (data.get('rendered') or {}).get('messages') or []
print()
print('=== 每条消息的字符数（按大小排序）===')
sizes = []
for i, m in enumerate(msgs):
    text = ''.join(
        (b.get('text') or '') for b in (m.get('blocks') or []) if isinstance(b, dict)
    )
    sizes.append((len(text), i, m.get('role'), text[:90].replace('\n', ' ')))
total = sum(s for s, _, _, _ in sizes)
print('  总计 %d 字符（约 %d tokens）' % (total, total // 2))
for size, i, role, head in sorted(sizes, reverse=True):
    print('  #%-3d %-10s %8d 字符  %s' % (i, role, size, head))

print()
print('=== 工具定义大小 ===')
tools = (data.get('rendered') or {}).get('tools') or []
tj = json.dumps(tools, ensure_ascii=False)
print('  工具数 %d，定义共 %d 字符（约 %d tokens）' % (len(tools), len(tj), len(tj)//2))
for t in tools[:30]:
    print('    %-34s %6d 字符' % (t.get('name'), len(json.dumps(t, ensure_ascii=False))))
PY
