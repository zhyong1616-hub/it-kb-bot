@echo off
chcp 65001 >nul
title IT Knowledge Base Bot
cd /d "%~dp0"

echo ============================================================
echo   IT Knowledge Base Bot - Starting
echo ============================================================
echo.

set "PYEXE="

where py >nul 2>nul
if not errorlevel 1 set "PYEXE=py"

if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE goto :no_python

if not exist ".env" goto :no_env

echo Starting bot... (keep this window open; close it to stop)
echo.
%PYEXE% -m app.main
echo.
echo Bot stopped.
pause
exit /b 0

:no_python
echo [ERROR] Python not found. Please run install.bat first.
echo.
pause
exit /b 1

:no_env
echo [ERROR] .env not found. Please run install.bat first.
echo.
pause
exit /b 1
