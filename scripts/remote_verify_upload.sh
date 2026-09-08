#!/bin/bash
# 校验上传的包，并找出启动命令
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== 插件包 ==="
ls -la --time-style=full-iso plugins/tavern_preset_regex*.mfp
sha256sum plugins/tavern_preset_regex-2.5.4.mfp

echo
echo "=== 启动命令线索 ==="
echo "--- tmux myapp 的完整 pane 命令 ---"
tmux list-panes -t myapp -F '#{pane_pid} #{pane_current_command} #{pane_start_command}' 2>/dev/null

echo "--- bash_history 里的启动命令 ---"
grep -nE "uv run|main.py|tmux" /root/.bash_history 2>/dev/null | tail -10

echo
echo "=== uv 是否可用 ==="
which uv
cd /root/Neo-MoFox_Deployment/Neo-MoFox && uv --version 2>&1 | head -2
