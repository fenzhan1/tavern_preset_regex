#!/bin/bash
# 检查启动后是否有新的主回复请求，并统计标签层级
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
KEY='Zcz199758!'

echo "=== 当前时间 ==="
date '+%H:%M:%S'

echo
echo "=== 最近的 LLM 请求 ==="
for f in $(ls -t logs/*.log 2>/dev/null | head -2); do
    grep -h "LLM 请求\] phase=" "$f" 2>/dev/null | tail -4
done

echo
echo "=== 请求检查器里的主回复请求 ==="
curl -s -m 20 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests" -o /tmp/rl.json
ID=$(.venv/bin/python3 -c "
import json
d=json.load(open('/tmp/rl.json',encoding='utf-8'))
data=d.get('data') or d
if isinstance(data,dict): data=data.get('requests') or data.get('items') or []
hits=[r for r in data if r.get('request_name')=='neo_default_chatter']
print(hits[-1]['id'] if hits else '')
")
echo "最新 id=$ID"
if [ -n "$ID" ]; then
    curl -s -m 30 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests/$ID" -o /tmp/reqY.json
    .venv/bin/python3 - <<'PY'
import json
d = json.load(open('/tmp/reqY.json', encoding='utf-8'))
data = d.get('data') or d
msgs = (data.get('rendered') or {}).get('messages') or []
print('ts=%s msgs=%d' % (data.get('ts_str'), len(msgs)))
print('每条消息的 <interactive_input> 层级：')
for i, m in enumerate(msgs):
    text = ''.join((b.get('text') or '') for b in (m.get('blocks') or []) if isinstance(b, dict))
    o = text.count('<interactive_input>')
    c = text.count('</interactive_input>')
    flag = '  <== 堆积!' if o > 1 else ''
    print('  #%-3d %-10s open=%d close=%d len=%d%s' % (i, m.get('role'), o, c, len(text), flag))
PY
fi
