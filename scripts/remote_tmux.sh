#!/bin/bash
# 部署 2.5.4 到线上并查看 tmux 会话
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1

echo "=== tmux 会话 myapp 的最后 20 行 ==="
tmux capture-pane -p -t myapp -S -20 2>/dev/null | tail -20

echo
echo "=== 当前插件包 ==="
ls -la plugins/tavern_preset_regex*.mfp

echo
echo "=== 2.5.4 是否已上传 ==="
ls -la plugins/tavern_preset_regex-2.5.4.mfp 2>/dev/null || echo "尚未上传"
