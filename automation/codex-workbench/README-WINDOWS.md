# Codex Workbench on Windows

This local dashboard reads project state from each project's `PROJECT_MAP.md`.
It runs on your PC at `http://127.0.0.1:57944`. It does not need a ChatGPT API
key and does not call a model when you browse the dashboard.

## Set up

1. Install Python 3.11 or newer and Git. Clone this repository to your PC.
2. Open PowerShell in `silicon-cortex\automation\codex-workbench`.
3. For a first run, create a small project map using your own directory:

```powershell
$projectRoot = Join-Path $HOME 'codex-workbench-demo'
New-Item -ItemType Directory -Force $projectRoot | Out-Null
py -3 .\workbench.py --data-dir .\.local init --root $projectRoot --project demo-project --title 'Demo project' --goal 'Track one outcome' --outcome first-outcome --outcome-title 'First outcome'
py -3 .\workbench.py --data-dir .\.local register --root $projectRoot --project demo-project
```

If `py` is unavailable, use `python` in these commands. To use a real project,
replace `$projectRoot` with its absolute directory path and choose stable IDs.
`init` refuses to overwrite an existing map. You can also add an existing valid
map from the dashboard's **管理项目** panel after previewing its binding.

4. Run `start-windows.cmd` and open `http://127.0.0.1:57944`. Keep its console
   window open while using the dashboard; close it or press Ctrl+C to stop.

The `.local` directory contains your private project bindings, locks, and
backups. It is ignored by Git. The optional quota and activity inputs remain
unavailable until you explicitly configure compatible local providers. ChatGPT
and Codex do not automatically supply those inputs or import your past tasks.

## Check the installation

From the same directory, run:

```powershell
py -3 -m unittest discover -s tests -v
```

The Windows start script runs the Python server directly. The existing macOS
`launchctl` scripts are for the original Mac installation only.
