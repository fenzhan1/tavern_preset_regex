#!/bin/bash
# 检查消息表是否重复堆积
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

.venv/bin/python3 - <<'PY'
import sqlite3, datetime
con = sqlite3.connect('file:data/MoFox.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

print('=== 总量 ===')
n = cur.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
print('  messages 总数:', n)

print()
print('=== 重复 message_id 最多的 10 个 ===')
for r in cur.execute("""
    SELECT message_id, COUNT(*) c, MIN(time) t0, MAX(time) t1,
           SUBSTR(MAX(COALESCE(processed_plain_text,'')),1,60) sample
    FROM messages GROUP BY message_id HAVING c > 1 ORDER BY c DESC LIMIT 10
"""):
    t0 = datetime.datetime.fromtimestamp(r['t0']).strftime('%m-%d %H:%M')
    t1 = datetime.datetime.fromtimestamp(r['t1']).strftime('%m-%d %H:%M')
    print('  x%-6d %s .. %s  %s' % (r['c'], t0, t1, (r['sample'] or '').replace('\n',' ')))

print()
print('=== 重复 (stream_id, content) 最多的 10 个 ===')
for r in cur.execute("""
    SELECT stream_id, COUNT(*) c, SUBSTR(content,1,60) sample
    FROM messages GROUP BY stream_id, content HAVING c > 1 ORDER BY c DESC LIMIT 10
"""):
    print('  x%-6d stream=%s  %s' % (r['c'], str(r['stream_id'])[:12], (r['sample'] or '').replace('\n',' ')))

print()
print('=== 主群 04bb9bb2ee 最近 20 条消息 ===')
for r in cur.execute("""
    SELECT message_id, time, person_id, message_type,
           SUBSTR(COALESCE(processed_plain_text, content), 1, 80) body
    FROM messages WHERE stream_id LIKE '04bb9bb2%' ORDER BY time DESC LIMIT 20
"""):
    ts = datetime.datetime.fromtimestamp(r['time']).strftime('%m-%d %H:%M:%S')
    print('  %s %-12s %-10s %s' % (ts, str(r['person_id'])[:10], r['message_type'], (r['body'] or '').replace('\n',' ')))

print()
print('=== 按天统计消息量（主群）===')
for r in cur.execute("""
    SELECT DATE(time, 'unixepoch', '+8 hours') d, COUNT(*) c
    FROM messages WHERE stream_id LIKE '04bb9bb2%'
    GROUP BY d ORDER BY d DESC LIMIT 14
"""):
    print('  %s  %d' % (r['d'], r['c']))
PY
