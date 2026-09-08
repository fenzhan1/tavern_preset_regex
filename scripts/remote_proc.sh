#!/bin/bash
# 查看线上进程如何启动/管理
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== 进程树 ==="
ps -o pid,ppid,lstart,cmd -p 2844
echo "--- 父进程 ---"
PPID_=$(ps -o ppid= -p 2844 | tr -d ' ')
echo "ppid=$PPID_"
ps -o pid,ppid,lstart,cmd -p "$PPID_" 2>/dev/null

echo
echo "=== 是否有 systemd 服务 ==="
systemctl list-units --type=service --no-pager --no-legend 2>/dev/null | grep -iE "mofox|neo" || echo "（没有 mofox/neo 服务）"
ls -la /etc/systemd/system/ 2>/dev/null | grep -iE "mofox|neo" || echo "（systemd 里没有）"

echo
echo "=== 是否有 supervisor / pm2 / screen / tmux ==="
which supervisorctl pm2 screen tmux 2>/dev/null
screen -ls 2>/dev/null | head -5
tmux ls 2>/dev/null | head -5

echo
echo "=== 启动脚本 ==="
ls -la /root/Neo-MoFox_Deployment/ 2>/dev/null
find /root/Neo-MoFox_Deployment -maxdepth 2 -name "*.sh" 2>/dev/null | head -10
