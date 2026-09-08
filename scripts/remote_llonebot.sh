#!/bin/bash
# 找 llonebot 容器内的 HTTP API
set -u
echo "=== 容器网络 ==="
docker inspect llonebot-llbot-1 --format '{{.NetworkSettings.IPAddress}} | {{json .NetworkSettings.Networks}}' 2>/dev/null | head -5

echo
echo "=== 容器内监听端口 ==="
docker exec llonebot-llbot-1 sh -c 'netstat -tlnp 2>/dev/null || ss -tlnp 2>/dev/null || cat /proc/net/tcp | head -20' 2>&1 | head -25

echo
echo "=== 容器内进程 ==="
docker exec llonebot-llbot-1 sh -c 'ps aux 2>/dev/null | head -15' 2>&1 | head -20

echo
echo "=== 尝试常见 API 端口 ==="
IP=$(docker inspect llonebot-llbot-1 --format '{{.NetworkSettings.IPAddress}}' 2>/dev/null)
echo "容器 IP: $IP"
for p in 3000 3001 5700 6099 8080; do
    code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "http://$IP:$p/" 2>/dev/null)
    echo "  $IP:$p -> $code"
done

echo
echo "=== 容器内配置文件 ==="
docker exec llonebot-llbot-1 sh -c 'ls -la /app 2>/dev/null; find / -maxdepth 3 -name "*.json" -path "*llbot*" 2>/dev/null | head -10' 2>&1 | head -25
