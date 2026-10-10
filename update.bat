@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title IT Knowledge Base Bot - Update

REM ============================================================
REM  One-click updater (local drop-folder mode).
REM
REM  HOW TO USE
REM    1. Put the new update .zip into the drop folder.
REM       Default:  <this folder>\..\updates   (created if missing)
REM       Or put a path in update_dir.txt next to this file.
REM       Override: set env KB_UPDATE_DIR before running.
REM    2. Double-click this file. Sit back.
REM
REM  WHAT IT DOES
REM    stop service -> backup current -> unzip -> overlay code
REM    (keeps .env and data\) -> restart -> show result.
REM
REM  SAFE: .env (your credentials) and data\ (kb.csv, owners,
REM        pending, audit) are NEVER overwritten.
REM ============================================================

set "HERE=%~dp0"
cd /d "%HERE%"
set "TASKNAME=IT_KB_Bot"
set "STAMP=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%-%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "STAMP=%STAMP: =0%"

echo ============================================================
echo   IT Knowledge Base Bot  -  One-click Update
echo ============================================================
echo.

REM ---- Drop folder ----
REM  Priority:  1) KB_UPDATE_DIR env  2) update_dir.txt next to this file
REM             3) built-in default  (see below)
if defined KB_UPDATE_DIR goto :dirset
if exist "%HERE%update_dir.txt" (
    for /f "usebackq tokens=* delims=" %%d in ("%HERE%update_dir.txt") do (
        set "LINE=%%d"
        if not defined KB_UPDATE_DIR (
            if not "!LINE!"=="" (
                if not "!LINE:~0,1!"=="#" (
                    if /i not "!LINE:~0,3!"=="REM" set "KB_UPDATE_DIR=!LINE!"
                )
            )
        )
    )
)
:dirset
if not defined KB_UPDATE_DIR set "KB_UPDATE_DIR=F:\KIDE_BOT_update"
if not exist "%KB_UPDATE_DIR%" mkdir "%KB_UPDATE_DIR%" >nul 2>nul

echo   Service dir : %HERE%
echo   Drop folder : %KB_UPDATE_DIR%
echo.

REM ---- Find the newest .zip in the drop folder ----
set "PKG="
for /f "delims=" %%f in ('dir /b /o-d "%KB_UPDATE_DIR%\*.zip" 2^>nul') do (
    if not defined PKG set "PKG=%KB_UPDATE_DIR%\%%f"
)
if not defined PKG (
    echo   [NO PACKAGE] No .zip found in the drop folder.
    echo.
    echo   Please:
    echo     1. Put the new update .zip into:
    echo        %KB_UPDATE_DIR%
    echo     2. Run this file again.
    echo.
    pause
    exit /b 1
)

echo   Package     : %PKG%
echo.

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
if not defined PYEXE (
    echo   [WARN] Python not found by name; will still update files,
    echo          but cannot restart automatically.
    echo          Run start.bat afterwards.
    echo.
)

REM ---- Step 1/6 : stop the service ----
echo   [1/6] Stopping service...
schtasks /End /TN "%TASKNAME%" >nul 2>nul
taskkill /F /IM python.exe /T >nul 2>nul
taskkill /F /IM pythonw.exe /T >nul 2>nul
timeout /t 2 /nobreak >nul
echo         done.

REM ---- Step 2/6 : backup current version (code only) ----
echo   [2/6] Backing up current version...
set "BAKDIR=%HERE%_backup\%STAMP%"
mkdir "%BAKDIR%" >nul 2>nul
robocopy "%HERE%app" "%BAKDIR%\app" /E /NFL /NDL /NJH /NJS /NC /NS >nul
copy /y "%HERE%*.bat" "%BAKDIR%\" >nul 2>nul
echo         backup -> %BAKDIR%

REM ---- Step 3/6 : extract the package ----
echo   [3/6] Extracting package...
set "EXTRACT=%HERE%_update_tmp"
if exist "%EXTRACT%" rmdir /s /q "%EXTRACT%" >nul 2>nul
mkdir "%EXTRACT%" >nul 2>nul
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%PKG%' -DestinationPath '%EXTRACT%' -Force" 2>nul
if errorlevel 1 (
    echo         [ERROR] Failed to extract the package.
    echo         Is it a valid .zip?  (Right-click - Properties - Unblock first.)
    echo.
    pause
    exit /b 1
)

REM ---- Locate the real code root inside the extracted tree ----
REM      Supports both  <zip>\app\...  and  <zip>\<subdir>\app\...
set "SRC=%EXTRACT%"
if not exist "%SRC%\app" (
    for /d %%d in ("%EXTRACT%\*") do (
        if exist "%%d\app" set "SRC=%%d"
    )
)
if not exist "%SRC%\app" (
    echo         [ERROR] The package does not look right (no app\ folder).
    echo         Nothing was changed.
    echo.
    rmdir /s /q "%EXTRACT%" >nul 2>nul
    pause
    exit /b 1
)
echo         source -> %SRC%

REM ---- Step 4/6 : overlay code, KEEP .env and data\ ----
echo   [4/6] Applying update (keeping .env and data)...
robocopy "%SRC%\app"    "%HERE%app"    /E /NFL /NDL /NJH /NJS /NC /NS >nul
robocopy "%SRC%\tools"  "%HERE%tools"  /E /NFL /NDL /NJH /NJS /NC /NS >nul
robocopy "%SRC%\tests"  "%HERE%tests"  /E /NFL /NDL /NJH /NJS /NC /NS >nul
copy /y "%SRC%\*.bat"   "%HERE%\"      >nul 2>nul
copy /y "%SRC%\*.md"    "%HERE%\"      >nul 2>nul
copy /y "%SRC%\requirements.txt" "%HERE%\" >nul 2>nul
copy /y "%SRC%\install.sh" "%HERE%\"   >nul 2>nul
echo         done.  (.env and data\ untouched)

REM ---- Step 5/6 : clean caches ----
echo   [5/6] Cleaning caches...
for /d /r "%HERE%app" %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" >nul 2>nul
rmdir /s /q "%EXTRACT%" >nul 2>nul
echo         done.

REM ---- Step 6/6 : restart the service ----
echo   [6/6] Restarting service...
if defined PYEXE (
    schtasks /Query /TN "%TASKNAME%" >nul 2>nul
    if errorlevel 1 (
        echo         no scheduled task; starting in a new window...
        start "" "%HERE%start.bat"
    ) else (
        schtasks /Run /TN "%TASKNAME%" >nul 2>nul
        echo         service restarted via task %TASKNAME%.
    )
) else (
    echo         skipped (Python not found). Run start.bat manually.
)

echo.
echo ============================================================
echo   DONE. Update applied.
echo.
echo   Package used : %PKG%
echo   Backup kept  : %BAKDIR%
echo   Verify       :  check_status.bat
echo   Roll back    :  copy files from the backup folder above
echo ============================================================
echo.
pause
exit /b 0
