#!/bin/bash
# 排查上下文不断堆积：请求消息数/ token 趋势 + 重复内容
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
KEY='Zcz199758!'

echo "=== 当前时间 ==="
date '+%Y-%m-%d %H:%M:%S'

echo
echo "=== 请求列表（全部，含 msg_count）==="
curl -s -m 20 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests" -o /tmp/rl.json
.venv/bin/python3 - <<'PY'
import json
d = json.load(open('/tmp/rl.json', encoding='utf-8'))
data = d.get('data') or d
if isinstance(data, dict):
    data = data.get('requests') or data.get('items') or []
print('共 %d 条' % len(data))
for r in data:
    print('  id=%-4s %-28s %-28s msgs=%-4s tools=%-4s tokens=%s' % (
        r.get('id'), r.get('model'), r.get('request_name'),
        r.get('msg_count'), r.get('tool_count'), r.get('estimated_input_tokens')))
PY

echo
echo "=== llm_stats: 最近的 neo_default_chatter 请求 token 趋势 ==="
.venv/bin/python3 - <<'PY'
import sqlite3
con = sqlite3.connect('file:data/llm_stats/llm_stats.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
rows = list(cur.execute("""
    SELECT id, timestamp, model_name, request_name, prompt_tokens, completion_tokens, total_tokens
    FROM llm_requests
    WHERE request_name = 'neo_default_chatter'
    ORDER BY id DESC LIMIT 25
"""))
for r in reversed(rows):
    import datetime
    ts = datetime.datetime.fromtimestamp(r['timestamp']).strftime('%m-%d %H:%M:%S')
    print('  %s  %-22s prompt=%-7s total=%-7s' % (ts, r['model_name'], r['prompt_tokens'], r['total_tokens']))
PY

echo
echo "=== 按 request_name 统计 token（最近 500 条）==="
.venv/bin/python3 - <<'PY'
import sqlite3
con = sqlite3.connect('file:data/llm_stats/llm_stats.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
for r in cur.execute("""
    SELECT request_name, COUNT(*) n, AVG(prompt_tokens) avg_p, MAX(prompt_tokens) max_p
    FROM (SELECT * FROM llm_requests ORDER BY id DESC LIMIT 500)
    GROUP BY request_name ORDER BY avg_p DESC
"""):
    print('  %-52s n=%-5s avg_prompt=%-9.0f max=%s' % (
        r['request_name'], r['n'], r['avg_p'] or 0, r['max_p']))
PY
