#!/bin/bash
# 对比同一轮内连续两个请求，看什么在增长
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
KEY='Zcz199758!'

.venv/bin/python3 - <<'PY'
import json, subprocess

def get(url):
    out = subprocess.run(
        ['curl', '-s', '-m', '30', '-H', 'X-API-Key: Zcz199758!', url],
        capture_output=True, text=True,
    ).stdout
    return json.loads(out)

lst = get('http://127.0.0.1:8000/webui/api/request-inspector/requests')
data = lst.get('data') or lst
if isinstance(data, dict):
    data = data.get('requests') or data.get('items') or []
ids = [r['id'] for r in data if r.get('request_name') == 'neo_default_chatter']
print('主回复请求 id:', ids)

def summarize(rid):
    d = get('http://127.0.0.1:8000/webui/api/request-inspector/requests/%d' % rid)
    dd = d.get('data') or d
    msgs = (dd.get('rendered') or {}).get('messages') or []
    total = 0
    per = []
    for i, m in enumerate(msgs):
        t = ''.join((b.get('text') or '') for b in (m.get('blocks') or []) if isinstance(b, dict))
        total += len(t)
        per.append((i, m.get('role'), len(t)))
    tools = (dd.get('rendered') or {}).get('tools') or []
    tlen = len(json.dumps(tools, ensure_ascii=False))
    return dd, msgs, per, total, tlen

prev = None
for rid in ids[-4:]:
    dd, msgs, per, total, tlen = summarize(rid)
    print()
    print('=== id=%s %s msgs=%d 消息字符=%d 工具字符=%d ===' % (
        rid, dd.get('ts_str'), len(msgs), total, tlen))
    if prev is not None:
        prev_map = {i: (r, n) for i, r, n in prev[2]}
        print('  --- 与上一个请求对比 ---')
        for i, role, n in per:
            old = prev_map.get(i)
            if old and old[1] != n:
                print('    #%-3d %-10s %d -> %d (%+d)' % (i, role, old[1], n, n - old[1]))
            elif not old:
                print('    #%-3d %-10s 新增 %d' % (i, role, n))
    prev = (dd, msgs, per, total, tlen)
PY

echo
echo "=== 上下文压缩配置 ==="
grep -rn -iE "compress|compression|context.*limit|max_token|budget|history" config/plugins/neo_default_chatter/config.toml 2>/dev/null | head -30

echo
echo "=== 全局上下文管理配置 ==="
grep -rn -iE "context|token|compress" config/core.toml 2>/dev/null | head -25
