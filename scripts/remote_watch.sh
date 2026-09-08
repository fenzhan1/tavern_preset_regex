#!/bin/bash
# 查看重启后是否有新的主回复请求，并分析顺序
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
KEY='Zcz199758!'

echo "=== 当前时间 ==="
date '+%H:%M:%S'

echo
echo "=== 请求列表（最后 6 条）==="
curl -s -m 20 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests" -o /tmp/rl.json
.venv/bin/python3 - <<'PY'
import json
d = json.load(open('/tmp/rl.json', encoding='utf-8'))
data = d.get('data') or d
if isinstance(data, dict):
    data = data.get('requests') or data.get('items') or []
print('共', len(data), '条')
for r in data[-6:]:
    print('  id=%s %s %s' % (r.get('id'), r.get('model'), r.get('request_name')))
PY

echo
echo "=== 重启后的 LLM 请求 ==="
grep -rh "LLM 请求\] phase=" logs/mofox_20260909_05*.log 2>/dev/null | tail -6
ls -t logs/*.log | head -3
