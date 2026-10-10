@echo off
chcp 65001 >nul
title IT Knowledge Base Bot - Self Check
cd /d "%~dp0"

set "PYEXE="

where py >nul 2>nul
if not errorlevel 1 set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
    echo [ERROR] Python not found. Please run install.bat first.
    echo.
    pause
    exit /b 1
)

%PYEXE% tools\check.py
echo.
pause
