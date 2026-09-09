#!/bin/bash
# 检查插件包与加载器选择
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== plugins 目录里的 tavern 包 ==="
ls -la --time-style=full-iso plugins/tavern_preset_regex*.mfp

echo
echo "=== 加载器日志（本次启动）==="
grep -rh "tavern_preset_regex" logs/$(ls -t logs/ | head -1) 2>/dev/null | head -20

echo
echo "=== 最近解包目录 ==="
ls -dt /tmp/mofox_plugin_tavern_preset_regex_* 2>/dev/null | head -3
L=$(ls -dt /tmp/mofox_plugin_tavern_preset_regex_* 2>/dev/null | head -1)
echo "最新: $L"
grep -o '"version"[^,]*' "$L/manifest.json" 2>/dev/null
grep -c "_already_wrapped" "$L/engine.py" 2>/dev/null

echo
echo "=== 进程 ==="
pgrep -af "main.py" | head -3
