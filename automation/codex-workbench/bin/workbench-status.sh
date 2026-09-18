#!/bin/zsh
set -eu

port=57944
if /usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "运行中：http://127.0.0.1:$port（仅本机）。"
  exit 0
fi
echo "未运行。"
