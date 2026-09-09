#!/bin/bash
# 轻量检查：只查总量与主群最近消息，避免全表扫描超时
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

.venv/bin/python3 - <<'PY'
import sqlite3, datetime
con = sqlite3.connect('file:data/MoFox.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
cur.execute('PRAGMA query_only = 1')

print('=== 总量 ===')
print('  messages 总数:', cur.execute('SELECT COUNT(*) FROM messages').fetchone()[0])

print()
print('=== 主群最近 15 条（按时间倒序）===')
for r in cur.execute("""
    SELECT message_id, time, person_id, message_type,
           SUBSTR(COALESCE(processed_plain_text, content), 1, 70) body
    FROM messages WHERE stream_id LIKE '04bb9bb2%' ORDER BY time DESC LIMIT 15
"""):
    ts = datetime.datetime.fromtimestamp(r['time']).strftime('%m-%d %H:%M:%S')
    print('  %s %-10s %-10s %s' % (ts, str(r['person_id'])[:10], r['message_type'], (r['body'] or '').replace('\n',' ')))

print()
print('=== 最近 300 条里重复 message_id ===')
for r in cur.execute("""
    SELECT message_id, COUNT(*) c FROM (
        SELECT message_id FROM messages WHERE stream_id LIKE '04bb9bb2%' ORDER BY time DESC LIMIT 300
    ) GROUP BY message_id HAVING c > 1 ORDER BY c DESC LIMIT 5
"""):
    print('  x%d  %s' % (r['c'], r['message_id']))

print()
print('=== 机器人自己发的消息数（最近 300 条中）===')
for r in cur.execute("""
    SELECT person_id, COUNT(*) c FROM (
        SELECT person_id FROM messages WHERE stream_id LIKE '04bb9bb2%' ORDER BY time DESC LIMIT 300
    ) GROUP BY person_id ORDER BY c DESC LIMIT 8
"""):
    print('  %-14s %d' % (str(r['person_id'])[:14], r['c']))
PY
