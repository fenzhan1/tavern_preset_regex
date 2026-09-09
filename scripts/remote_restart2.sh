#!/bin/bash
# 重启 bot 加载 2.5.5 并确认
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== 当前进程 ==="
pgrep -af "main.py" | head -3

echo
echo "=== 重启 ==="
tmux send-keys -t myapp C-c
sleep 8
pgrep -af "main.py" >/dev/null 2>&1 && { echo "还在跑，再等"; sleep 6; }
tmux send-keys -t myapp "uv run main.py" Enter
sleep 28

echo
echo "=== 加载的插件版本 ==="
grep -rh "插件加载成功: tavern_preset_regex" logs/*.log 2>/dev/null | tail -2

echo
echo "=== 新进程 ==="
pgrep -af "main.py" | head -3

echo
echo "=== pane 末尾 ==="
tmux capture-pane -p -t myapp -S -12 2>/dev/null | tail -12
