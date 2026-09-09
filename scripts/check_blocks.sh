#!/bin/bash
# 列出请求 #11 里 #3 历史块的全部 block 与 system_reminder 标题
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
KEY='Zcz199758!'

curl -s -m 30 -H "X-API-Key: $KEY" "http://127.0.0.1:8000/webui/api/request-inspector/requests/11" -o /tmp/r11.json
.venv/bin/python3 - <<'PY'
import json, re
d = json.load(open('/tmp/r11.json', encoding='utf-8'))
data = d.get('data') or d
msgs = (data.get('rendered') or {}).get('messages') or []
print('总消息数:', len(msgs))
for mi, m in enumerate(msgs):
    blocks = [b.get('text') or '' for b in (m.get('blocks') or []) if isinstance(b, dict)]
    total = sum(len(b) for b in blocks)
    print('\n=== #%d %s blocks=%d 总长=%d ===' % (mi, m.get('role'), len(blocks), total))
    for bi, b in enumerate(blocks):
        titles = re.findall(r'\[([^\]\n]{1,40})\]', b[:400])
        first = b.replace('\n', ' ')[:100]
        print('  [%d] len=%-6d 标题=%s' % (bi, len(b), titles[:3]))
        print('       %s' % first)
PY
