@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title IT Knowledge Base Bot (Service)
cd /d "%~dp0"

REM ============================================================
REM  Headless runner used by the scheduled task (auto-start on boot).
REM  - No "pause": it must never wait for a key press.
REM  - Auto-restart the bot if it exits unexpectedly.
REM  - All output goes to logs\bot-YYYYMMDD.log
REM  Do NOT double-click this file; use start.bat for manual runs.
REM ============================================================

set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>nul

REM ---- One log file per day ----
for /f "tokens=1-3 delims=/- " %%a in ("%DATE%") do set "D=%%c%%b%%a"
set "LOG=%LOGDIR%\bot-%D%.log"

REM ---- Locate Python (skip the Microsoft Store stub) ----
set "PYEXE="

py -3 -c "import sys;print(sys.version_info[0])" >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
)

if not defined PYEXE (
    python -c "import sys;print(sys.version_info[0])" >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
    )
)

if not defined PYEXE call :scan "%LOCALAPPDATA%\Programs\Python"
if not defined PYEXE call :scan "%ProgramFiles%\Python"
if not defined PYEXE call :scan "C:\Python311"
if not defined PYEXE call :scan "C:\Python312"
if not defined PYEXE call :scan "C:\Python310"

if not defined PYEXE (
    echo [%DATE% %TIME%] [ERROR] Python not found. Run install.bat first.>>"%LOG%"
    exit /b 1
)

if not exist "%~dp0.env" (
    echo [%DATE% %TIME%] [ERROR] .env not found. Run install.bat first.>>"%LOG%"
    exit /b 1
)

echo.>>"%LOG%"
echo ============================================================>>"%LOG%"
echo [%DATE% %TIME%] Service starting  (python=%PYEXE%)>>"%LOG%"
echo ============================================================>>"%LOG%"

REM ---- Run bot; restart automatically if it dies ----
:loop
echo [%DATE% %TIME%] bot process launching...>>"%LOG%"
"%PYEXE%" -u -m app.main >>"%LOG%" 2>&1
set "RC=!errorlevel!"
echo [%DATE% %TIME%] bot process exited, code=!RC!.>>"%LOG%"

REM Exit code 42 = "restart for self-update" -> restart quickly, no long cooldown.
if "!RC!"=="42" (
    echo [%DATE% %TIME%] self-update requested; restarting in 2s...>>"%LOG%"
    timeout /t 2 /nobreak >nul
    goto loop
)

REM If it exits very fast repeatedly we could spin; add a short cool-down.
timeout /t 10 /nobreak >nul
goto loop


REM ================= Subroutine =================
:scan
set "SCANDIR=%~1"
if not defined SCANDIR exit /b 0
if not exist "%SCANDIR%" exit /b 0
for /d %%d in ("%SCANDIR%\Python3*") do (
    if exist "%%d\python.exe" (
        if not defined PYEXE (
            "%%d\python.exe" -c "import sys;print(sys.version_info[0])" >nul 2>nul
            if not errorlevel 1 set "PYEXE=%%d\python.exe"
        )
    )
)
exit /b 0
