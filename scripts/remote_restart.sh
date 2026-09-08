#!/bin/bash
# 找到 uv 并重启 bot（tmux myapp）
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

UV=$(ls /root/.local/bin/uv 2>/dev/null || which uv 2>/dev/null || echo "")
if [ -z "$UV" ]; then
    UV=$(find /root -maxdepth 4 -name uv -type f 2>/dev/null | head -1)
fi
echo "uv 路径: $UV"
[ -n "$UV" ] && "$UV" --version

echo
echo "=== 重启前进程 ==="
ps -o pid,lstart,cmd -p 2844 2>/dev/null

echo
echo "=== 发送 Ctrl-C 到 tmux myapp ==="
tmux send-keys -t myapp C-c 2>&1 || echo "send-keys 失败"
sleep 6

echo "=== 旧进程是否退出 ==="
if ps -p 2844 >/dev/null 2>&1; then
    echo "仍在运行，再等 6 秒"
    sleep 6
fi
ps -o pid,cmd -p 2844 2>/dev/null || echo "已退出"

echo
echo "=== 重新启动 ==="
tmux send-keys -t myapp "uv run main.py" Enter
sleep 25

echo "=== 启动后 pane 末尾 ==="
tmux capture-pane -p -t myapp -S -25 2>/dev/null | tail -25

echo
echo "=== 新进程 ==="
pgrep -af "main.py" | head -5
