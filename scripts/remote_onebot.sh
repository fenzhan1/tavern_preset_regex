#!/bin/bash
# 找 OneBot 适配器的 HTTP API 端口，用于发测试消息
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== onebot 配置 ==="
find config -path "*onebot*" -type f 2>/dev/null | head -5
cat config/plugins/onebot_adapter/config.toml 2>/dev/null | grep -nE "host|port|url|token|ws|http" | head -25

echo
echo "=== 监听端口（全部）==="
ss -tlnp | awk 'NR==1 || /LISTEN/' | head -30

echo
echo "=== docker 容器端口映射 ==="
docker ps --format '{{.Names}}\t{{.Ports}}'

echo
echo "=== llonebot 容器端口 ==="
docker inspect llonebot-llbot-1 --format '{{json .NetworkSettings.Ports}}' 2>/dev/null
