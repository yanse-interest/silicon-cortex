#!/bin/zsh
set -eu

label="org.siliconcortex.codex-workbench"
domain="gui/$(id -u)"
/bin/launchctl bootout "$domain/$label" 2>/dev/null || true
echo "Codex Workbench 已停止；项目地图和私有数据未修改。"
