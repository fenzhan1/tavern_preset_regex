#!/bin/bash
# 对比多个请求：块数、长度、标签层级随时间的变化
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
KEY='Zcz199758!'

curl -s -m 20 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests" -o /tmp/rl.json
.venv/bin/python3 - <<'PY'
import json, subprocess, re

def get(url):
    out = subprocess.run(
        ['curl', '-s', '-m', '30', '-H', 'X-API-Key: Zcz199758!', url],
        capture_output=True, text=True).stdout
    return json.loads(out)

lst = get('http://127.0.0.1:8000/webui/api/request-inspector/requests')
data = lst.get('data') or lst
if isinstance(data, dict):
    data = data.get('requests') or data.get('items') or []
ids = [r['id'] for r in data if r.get('request_name') == 'neo_default_chatter']
print('主回复请求:', ids)

for rid in ids:
    d = get('http://127.0.0.1:8000/webui/api/request-inspector/requests/%d' % rid)
    dd = d.get('data') or d
    msgs = (dd.get('rendered') or {}).get('messages') or []
    total = 0
    blocks_total = 0
    max_open = 0
    convo_blocks = 0
    for m in msgs:
        blocks = [b.get('text') or '' for b in (m.get('blocks') or []) if isinstance(b, dict)]
        blocks_total += len(blocks)
        for b in blocks:
            total += len(b)
            max_open = max(max_open, b.count('<interactive_input>'))
            if 'system_reminder' in b or 'conversation_context' in b:
                convo_blocks += 1
    print('  id=%-4s %s msgs=%-3d blocks=%-3d 字符=%-7d 单块最多标签=%d 上下文块=%d' % (
        rid, dd.get('ts_str'), len(msgs), blocks_total, total, max_open, convo_blocks))
PY
