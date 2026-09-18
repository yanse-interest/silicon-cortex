#!/bin/zsh
set -eu

workbench="$(cd "$(dirname "$0")/.." && pwd -P)"
python="$(command -v python3)"
"$python" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
label="org.siliconcortex.codex-workbench"
domain="gui/$(id -u)"
runtime="$workbench/.local/runtime"
destination="$HOME/Library/LaunchAgents/$label.plist"

if [[ "${1:-}" == "--render-only" ]]; then
  [[ $# -eq 2 ]] || { echo "Usage: $0 --render-only DESTINATION" >&2; exit 2; }
  destination="$2"
else
  [[ $# -eq 0 ]] || { echo "Usage: $0 [--render-only DESTINATION]" >&2; exit 2; }
fi

mkdir -p "$runtime" "$(dirname "$destination")"
"$python" - "$python" "$workbench" "$runtime" "$destination" "$label" <<'PY'
import plistlib
import sys
from pathlib import Path

python, workbench, runtime, destination, label = sys.argv[1:]
payload = {
    "Label": label,
    "ProgramArguments": [python, str(Path(workbench) / "web.py"), "--data-dir", str(Path(workbench) / ".local"), "--host", "127.0.0.1", "--port", "57944"],
    "WorkingDirectory": workbench,
    "RunAtLoad": False,
    "StandardOutPath": str(Path(runtime) / "launchd.stdout.log"),
    "StandardErrorPath": str(Path(runtime) / "launchd.stderr.log"),
}
with open(destination, "wb") as handle:
    plistlib.dump(payload, handle)
PY
/usr/bin/plutil -lint "$destination" >/dev/null
if [[ "${1:-}" == "--render-only" ]]; then
  echo "已生成 $destination"
  exit 0
fi
/bin/launchctl bootout "$domain/$label" 2>/dev/null || true
/bin/launchctl bootstrap "$domain" "$destination"
/bin/launchctl kickstart -k "$domain/$label"
echo "已启动 $label：http://127.0.0.1:57944"
