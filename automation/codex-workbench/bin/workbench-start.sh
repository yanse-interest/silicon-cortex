#!/bin/zsh
set -eu

workbench="$(cd "$(dirname "$0")/.." && pwd -P)"
port=57944
if /usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 $port 已有服务监听；请打开 http://127.0.0.1:$port 核对。"
  exit 0
fi
"$workbench/bin/workbench-install-service.sh"
"$workbench/bin/workbench-status.sh"
