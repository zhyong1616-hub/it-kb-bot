@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title IT Knowledge Base Bot - Installer

echo ============================================================
echo   IT Knowledge Base Bot - Installer
echo ============================================================
echo.

cd /d "%~dp0"

set "PYEXE="

REM ---------- 1. Find a REAL Python ----------
echo [1/4] Checking Python...
echo.

REM Try "py" launcher first (most reliable on Windows)
py -3 -c "import sys;print(sys.version_info[0])" >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
    if defined PYEXE echo       OK: found "py" launcher
)

REM Try python.exe, but VERIFY it really works (skip Microsoft Store stub)
if not defined PYEXE (
    python -c "import sys;print(sys.version_info[0])" >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
        if defined PYEXE echo       OK: found "python" command
    ) else (
        echo       Note: "python" exists but does NOT run - that is the
        echo             Microsoft Store stub. Ignoring it.
    )
)

REM Try common install locations directly
if not defined PYEXE call :scan "%LOCALAPPDATA%\Programs\Python"
if not defined PYEXE call :scan "%ProgramFiles%\Python"
if not defined PYEXE call :scan "C:\Python311"
if not defined PYEXE call :scan "C:\Python312"
if not defined PYEXE call :scan "C:\Python310"
if not defined PYEXE call :scan "C:\Python39"

if defined PYEXE goto :got_python

REM ---------- 2. No working Python: download and install ----------
echo.
echo       No working Python found.
echo       Downloading Python 3.11.9 (about 25 MB) ...
echo.
set "PYURL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
set "PYINS=%TEMP%\python-3.11.9-amd64.exe"

if exist "%PYINS%" del /q "%PYINS%" >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYURL%' -OutFile '%PYINS%' -UseBasicParsing } catch { exit 1 }"

if not exist "%PYINS%" goto :dl_fail

echo       Download OK. Installing Python silently, please wait 2-3 minutes...
echo.
"%PYINS%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
if errorlevel 1 goto :py_fail

echo.
echo       Python installer finished. Verifying...

set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%"

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

if not defined PYEXE goto :path_fail

:got_python
echo       Python path: %PYEXE%

REM ---------- 3. Install dependencies ----------
echo.
echo [2/4] Installing dependencies...
"%PYEXE%" -m pip install --upgrade pip --quiet --disable-pip-version-check 2>nul
"%PYEXE%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo       Trying Tsinghua mirror...
    "%PYEXE%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet --disable-pip-version-check
    if errorlevel 1 goto :pip_fail
)
echo       OK.

REM ---------- 4. Create .env ----------
echo.
echo [3/4] Preparing config file (.env)...
if exist ".env" (
    echo       .env already exists, kept unchanged.
) else (
    copy /y ".env.example" ".env" >nul
    echo       .env created. Fill in WECOM_BOT_ID and WECOM_BOT_SECRET later.
)

REM ---------- 5. Init knowledge base ----------
echo.
echo [4/4] Initializing knowledge base data...
"%PYEXE%" tools\init_data.py
if errorlevel 1 goto :init_fail

echo.
echo ============================================================
echo   Install finished successfully!
echo.
echo   NEXT STEPS:
echo     1. Open .env with Notepad, fill in:
echo          WECOM_BOT_ID=...
echo          WECOM_BOT_SECRET=...
echo          KB_MOCK=0
echo        Save with encoding = UTF-8.
echo     2. Double-click  start.bat  to run the bot.
echo ============================================================
echo.
pause
exit /b 0


REM ================= Subroutine =================
:scan
set "SCANDIR=%~1"
if not defined SCANDIR exit /b 0
if not exist "%SCANDIR%" exit /b 0
for /d %%d in ("%SCANDIR%\Python3*") do (
    if exist "%%d\python.exe" (
        if not defined PYEXE (
            "%%d\python.exe" -c "import sys;print(sys.version_info[0])" >nul 2>nul
            if not errorlevel 1 (
                set "PYEXE=%%d\python.exe"
                echo       OK: found in %SCANDIR%
            )
        )
    )
)
exit /b 0


REM ================= Error handlers =================
:dl_fail
echo.
echo   [ERROR] Download failed. Your network may block python.org.
echo   Please download manually from:
echo       %PYURL%
echo   Run it and CHECK "Add python.exe to PATH", then run install.bat again.
echo.
pause
exit /b 1

:py_fail
echo.
echo   [ERROR] Python installation failed.
echo   Try: right-click install.bat - "Run as administrator".
echo.
pause
exit /b 1

:path_fail
echo.
echo   [ERROR] Python installed but still not found.
echo   Please CLOSE this window, open a NEW one, then run install.bat again.
echo.
pause
exit /b 1

:pip_fail
echo.
echo   [ERROR] Failed to install dependencies (websockets).
echo   Check your network, then run install.bat again.
echo.
pause
exit /b 1

:init_fail
echo.
echo   [ERROR] Failed to initialize knowledge base data.
echo.
pause
exit /b 1
