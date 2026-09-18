@echo off
setlocal
set "WORKBENCH_DIR=%~dp0"
set "DATA_DIR=%WORKBENCH_DIR%.local"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
echo Codex Workbench: http://127.0.0.1:57944
echo Close this window or press Ctrl+C to stop the local server.
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 "%WORKBENCH_DIR%web.py" --data-dir "%DATA_DIR%" --port 57944
exit /b %errorlevel%
:use_python
python "%WORKBENCH_DIR%web.py" --data-dir "%DATA_DIR%" --port 57944
exit /b %errorlevel%
