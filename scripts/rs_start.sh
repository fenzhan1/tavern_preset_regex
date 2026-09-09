#!/bin/bash
# 在 tmux 中重新启动 mofox
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
export PATH="/root/.local/bin:$PATH"

echo "=== uv ==="
which uv && uv --version

echo
echo "=== 可用插件包 ==="
ls -la plugins/tavern_preset_regex*.mfp

echo
echo "=== 创建 tmux 会话 myapp 并启动 ==="
tmux new-session -d -s myapp -c /root/Neo-MoFox_Deployment/Neo-MoFox
sleep 2
tmux send-keys -t myapp "export PATH=/root/.local/bin:\$PATH" Enter
sleep 1
tmux send-keys -t myapp "uv run main.py" Enter

echo "已发送启动命令，等待 35 秒..."
sleep 35

echo
echo "=== pane 末尾 ==="
tmux capture-pane -p -t myapp -S -30 2>/dev/null | tail -30

echo
echo "=== 进程 ==="
pgrep -af "main.py" | head -5
