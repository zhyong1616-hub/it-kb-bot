@echo off
chcp 65001 >nul
title IT Knowledge Base Bot - Status

set "TASKNAME=IT_KB_Bot"

echo ============================================================
echo   Auto-Start Status
echo ============================================================
echo.

echo [Scheduled task]
schtasks /Query /TN "%TASKNAME%" /V /FO LIST 2>nul | findstr /I "TaskName Status Next Last Result Run:"
if errorlevel 1 (
    echo   NOT INSTALLED.
    echo   Run setup_autostart.bat to enable auto-start.
)
echo.

echo [Processes]
tasklist /FI "IMAGENAME eq python.exe" 2>nul | findstr /I "python.exe"
tasklist /FI "IMAGENAME eq pythonw.exe" 2>nul | findstr /I "pythonw.exe"
echo   ^(if nothing above, the bot is NOT running^)
echo.

echo [Recent log lines]
for /f "delims=" %%f in ('dir /b /o-d "%~dp0logs\bot-*.log" 2^>nul') do (
    echo   --- logs\%%f ---
    powershell -NoProfile -Command "Get-Content '%~dp0logs\%%f' -Tail 12" 2>nul
    goto :done
)
echo   no log files yet.
:done
echo.
pause
exit /b 0
