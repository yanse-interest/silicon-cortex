#!/bin/zsh
set -eu

label="org.siliconcortex.codex-workbench"
domain="gui/$(id -u)"
destination="$HOME/Library/LaunchAgents/$label.plist"
/bin/launchctl bootout "$domain/$label" 2>/dev/null || true
/bin/rm -f "$destination"
echo "已卸载 $label；项目地图和私有数据未修改。"
