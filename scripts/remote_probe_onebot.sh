#!/bin/bash
# 探测 llonebot 的 OneBot HTTP API
set -u
echo "=== llbot 配置里的 http/port/token ==="
docker exec llonebot-llbot-1 sh -c 'grep -oE "\"(http|port|host|token|accessToken|enable)[^,]*" /app/llbot/default_config.json 2>/dev/null | head -30'
docker exec llonebot-llbot-1 sh -c 'ls -la /app/llbot/data 2>/dev/null | head -20'

echo
echo "=== 探测 3080 上的 OneBot 端点 ==="
for ep in "/" "/api" "/get_login_info" "/send_group_msg" "/api/get_login_info"; do
    code=$(curl -s -m 4 -o /tmp/ob.txt -w "%{http_code}" "http://127.0.0.1:3080$ep" 2>/dev/null)
    echo "  $ep -> $code : $(head -c 120 /tmp/ob.txt 2>/dev/null | tr -d '\n')"
done

echo
echo "=== 找 llbot 的 http 服务配置 ==="
docker exec llonebot-llbot-1 sh -c 'find / -maxdepth 4 -name "*.json" 2>/dev/null | grep -iE "config|setting" | head -10'
docker exec llonebot-llbot-1 sh -c 'cat /app/llbot/default_config.json 2>/dev/null | head -60'
