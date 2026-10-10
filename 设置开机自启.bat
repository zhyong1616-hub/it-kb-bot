@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title IT Knowledge Base Bot - Auto-Start Setup

set "TASKNAME=IT_KB_Bot"
set "HERE=%~dp0"
set "RUNNER=%HERE%run_service.bat"

echo ============================================================
echo   Auto-Start Setup (run at boot / logon)
echo ============================================================
echo.
echo   Task name : %TASKNAME%
echo   Runner    : %RUNNER%
echo.

if not exist "%RUNNER%" (
    echo [ERROR] run_service.bat not found next to this script.
    echo.
    pause
    exit /b 1
)

if not exist "%HERE%.env" (
    echo [WARN] .env not found. The bot will not start until you
    echo        fill in WECOM_BOT_ID / WECOM_BOT_SECRET.
    echo.
)

echo [1/3] Removing any previous task...
schtasks /Query /TN "%TASKNAME%" >nul 2>nul
if not errorlevel 1 (
    schtasks /Delete /TN "%TASKNAME%" /F >nul 2>nul
    echo       old task removed.
) else (
    echo       no existing task.
)

echo.
echo [2/3] Creating task (runs at logon + retries if it stops)...
REM  /SC ONLOGON    -> starts when this user logs on after a reboot
REM  /RL LIMITED    -> normal privileges (no admin prompt needed)
REM  /IT            -> interactive: keeps running with the desktop session
schtasks /Create /TN "%TASKNAME%" /TR "\"%RUNNER%\"" ^
    /SC ONLOGON /RL LIMITED /IT /F

if errorlevel 1 (
    echo.
    echo       ONLOGON failed, trying ONSTART ^+ highest privileges...
    schtasks /Create /TN "%TASKNAME%" /TR "\"%RUNNER%\"" ^
        /SC ONSTART /RU "%USERNAME%" /RL HIGHEST /F
    if errorlevel 1 goto :fail
)

echo.
echo [3/3] Starting it now, so you don't have to reboot...
schtasks /Run /TN "%TASKNAME%" >nul 2>nul
if errorlevel 1 (
    echo       (could not start now - it will start automatically next boot)
) else (
    echo       started.
)

echo.
echo ============================================================
echo   DONE. The bot will now start automatically.
echo.
echo   Check status :  check_status.bat
echo   View logs    :  logs\bot-YYYYMMDD.log
echo   Remove       :  remove_autostart.bat
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo   [ERROR] Could not create the scheduled task.
echo   Try: right-click this file - "Run as administrator".
echo.
pause
exit /b 1
