@echo off
rem Install Pitwall for this Windows user: Start-menu and Desktop shortcuts.
rem Double-click this file. Options: --no-desktop, --uninstall
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Pitwall needs Python 3.11 or newer, and it isn't installed.
    echo Get it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^),
    echo then double-click this file again.
    pause
    exit /b 1
)

%PY% "%~dp0tools\install_windows.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
