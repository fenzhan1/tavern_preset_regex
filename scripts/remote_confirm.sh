#!/bin/bash
# 确认 2.5.4 已加载，并检查解包目录
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== 插件加载记录 ==="
grep -rh "插件加载成功: tavern_preset_regex" logs/*.log 2>/dev/null | tail -3

echo
echo "=== 最新解包目录 ==="
L=$(ls -dt /tmp/mofox_plugin_tavern_preset_regex_* 2>/dev/null | head -1)
echo "$L"
grep -o '"version"[^,]*' "$L/manifest.json" 2>/dev/null
echo "--- 幂等重排标记 ---"
grep -c "_split_injected_presets" "$L/event_handler.py" 2>/dev/null
grep -n "_contains_setvar_marker(payloads)" "$L/event_handler.py" 2>/dev/null | head -3

echo
echo "=== 当前进程 ==="
pgrep -af "main.py" | head -3

echo
echo "=== 最近的 LLM 请求 ==="
grep -rh "LLM 请求\] phase=" logs/*.log 2>/dev/null | tail -5
