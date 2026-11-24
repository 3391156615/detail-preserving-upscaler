@echo off
rem ===========================================================================
rem  PackForDelivery.cmd - build the portable zip for other people.
rem  Read the summary printed at the end; nothing in this folder is modified.
rem ===========================================================================
setlocal EnableExtensions
pushd "%~dp0" 2>nul
if errorlevel 1 (
    echo [ERROR] Cannot enter the folder "%~dp0".
    pause
    exit /b 1
)
chcp 65001 >nul 2>nul
title Detail-Preserving Image Upscaling System - pack for delivery

where powershell >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Windows PowerShell was not found on this machine.
    popd
    pause
    exit /b 1
)

if not exist "%~dp0tools\pack_for_delivery.ps1" (
    echo [ERROR] tools\pack_for_delivery.ps1 is missing.
    popd
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\pack_for_delivery.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
    echo [ERROR] Packaging failed. Exit code: %RC%
    popd
    pause
    exit /b %RC%
)
popd
pause
exit /b 0
