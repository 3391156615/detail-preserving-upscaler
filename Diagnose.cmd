@echo off
rem ===========================================================================
rem  Diagnose.cmd - read-only environment report for this project.
rem  Nothing is installed, nothing is launched. Send the output to the author
rem  if Start.cmd fails.
rem ===========================================================================
setlocal
cd /d "%~dp0"
chcp 65001 >nul 2>nul
title Detail-Preserving Image Upscaling System - diagnostics

where powershell >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Windows PowerShell was not found on this machine.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Check
echo.
echo ---- recent startup.log ----
if exist "%~dp0logs\startup.log" (
    powershell -NoProfile -Command "Get-Content '%~dp0logs\startup.log' -Tail 30"
) else (
    echo [none]
)
echo.
pause
