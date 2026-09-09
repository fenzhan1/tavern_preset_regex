#!/bin/bash
# 检查并重启 tmux 里的 mofox
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== tmux 会话 ==="
tmux ls 2>&1

echo
echo "=== 当前进程 ==="
pgrep -af "main.py" | head -5 || echo "（没有 main.py 在跑）"

echo
echo "=== 各会话 pane 末尾 ==="
for s in $(tmux ls -F '#{session_name}' 2>/dev/null); do
    echo "--- session $s ---"
    tmux capture-pane -p -t "$s" -S -8 2>/dev/null | tail -8
done
