#!/bin/bash
# 分析堆积来源：消息表、压缩、历史注入
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== MoFox.db 表结构 ==="
.venv/bin/python3 - <<'PY'
import sqlite3
con = sqlite3.connect('file:data/MoFox.db?mode=ro', uri=True)
cur = con.cursor()
for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    n = r[0]
    try:
        cnt = cur.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
    except Exception:
        cnt = '?'
    cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{n}")')]
    print('  %-28s %-10s %s' % (n, cnt, cols))
PY

echo
echo "=== messages 表：每个 stream 的消息数与时间范围 ==="
.venv/bin/python3 - <<'PY'
import sqlite3, datetime
con = sqlite3.connect('file:data/MoFox.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
for r in cur.execute("""
    SELECT stream_id, COUNT(*) n, MIN(time) t0, MAX(time) t1,
           SUM(LENGTH(COALESCE(processed_plain_text,''))) chars
    FROM messages GROUP BY stream_id ORDER BY n DESC LIMIT 10
"""):
    print('  stream=%s n=%-6s chars=%-10s %s .. %s' % (
        str(r['stream_id'])[:16], r['n'], r['chars'], r['t0'], r['t1']))
PY

echo
echo "=== 最近 2 小时的 neo_default_chatter 请求（含压缩）==="
.venv/bin/python3 - <<'PY'
import sqlite3, datetime
con = sqlite3.connect('file:data/llm_stats/llm_stats.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
for r in cur.execute("""
    SELECT id, timestamp, model_name, request_name, prompt_tokens, stream_id
    FROM llm_requests
    WHERE request_name LIKE 'neo_default_chatter%' AND timestamp > ?
    ORDER BY id
""", ((datetime.datetime.now() - datetime.timedelta(hours=3)).timestamp(),)):
    ts = datetime.datetime.fromtimestamp(r['timestamp']).strftime('%H:%M:%S')
    print('  %s %-46s prompt=%-8s stream=%s' % (
        ts, r['request_name'], r['prompt_tokens'], str(r['stream_id'])[:10]))
PY
