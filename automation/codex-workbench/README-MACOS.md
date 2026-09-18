# Codex Workbench on macOS

This local dashboard reads project state from each project's `PROJECT_MAP.md`.
It runs only on your Mac at `http://127.0.0.1:57944`. Browsing does not call a
model or require an API key.

## Set up

1. Install Python 3.11 or newer and Git. Clone this repository.
2. Open Terminal in `silicon-cortex/automation/codex-workbench`.
3. To try it with your own sample directory, run:

```sh
project_root="$HOME/codex-workbench-demo"
mkdir -p "$project_root"
python3 workbench.py --data-dir .local init --root "$project_root" --project demo-project --title 'Demo project' --goal 'Track one outcome' --outcome first-outcome --outcome-title 'First outcome'
python3 workbench.py --data-dir .local register --root "$project_root" --project demo-project
```

For a real project, set `project_root` to its absolute directory and use your
own stable IDs. `init` refuses to overwrite an existing map. An existing valid
map can also be added from the dashboard's **管理项目** panel after previewing the
binding.

4. Run `bin/workbench-start.sh` and open `http://127.0.0.1:57944`.

The script creates a user LaunchAgent for this checkout using its actual path
and the selected `python3` executable. It does not start automatically at
login. Use `bin/workbench-status.sh`, `bin/workbench-stop.sh`, or
`bin/workbench-uninstall-service.sh` as needed. The service label is
`org.siliconcortex.codex-workbench`.

An older local installation may already listen on port 57944 under a different
LaunchAgent label. `workbench-start.sh` leaves that running service alone. Stop
the older service using its original stop command before starting this one;
the scripts here do not remove another installation's LaunchAgent or data.

The `.local` directory holds your private project bindings, locks, backups,
and service logs; Git ignores it. Optional quota and activity inputs remain
unavailable until you explicitly configure compatible local providers.
ChatGPT and Codex do not automatically import your past tasks.

## Check the installation

```sh
python3 -m unittest discover -s tests -v
```
