@echo off
chcp 65001 >nul
title IT Knowledge Base Bot - Remove Auto-Start

set "TASKNAME=IT_KB_Bot"

echo ============================================================
echo   Remove Auto-Start
echo ============================================================
echo.

schtasks /Query /TN "%TASKNAME%" >nul 2>nul
if errorlevel 1 (
    echo   Task "%TASKNAME%" is not installed. Nothing to do.
    echo.
    pause
    exit /b 0
)

echo   Stopping and deleting task "%TASKNAME%" ...
schtasks /End /TN "%TASKNAME%" >nul 2>nul
schtasks /Delete /TN "%TASKNAME%" /F >nul 2>nul

if errorlevel 1 (
    echo   [ERROR] Failed to delete. Try "Run as administrator".
    echo.
    pause
    exit /b 1
)

echo   Done. The bot will no longer start automatically.
echo   (Use start.bat to run it manually.)
echo.
pause
exit /b 0
