#!/bin/bash
# 恢复 debug_log=false，清理临时脚本
set -u
cd /root/Neo-MoFox_Deployment/Neo-MoFox || exit 1
CFG=config/plugins/tavern_preset_regex/config.toml
sed -i 's/^debug_log = true/debug_log = false/' "$CFG"
grep -n "debug_log" "$CFG"
rm -f /tmp/_dsh_*.sh
echo "已清理临时脚本"
ls /tmp/_dsh_*.sh 2>/dev/null || echo "（无残留）"
