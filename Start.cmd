@echo off
rem ===========================================================================
rem  Detail-Preserving Image Upscaling System - single entry point
rem  Double click this file. It delegates everything to launcher.ps1.
rem  Keep this file pure ASCII and CRLF: it must run on any Windows code page.
rem ===========================================================================
setlocal EnableExtensions
title Detail-Preserving Image Upscaling System

rem pushd maps a temporary drive letter for UNC paths (\\server\share\...),
rem where "cd /d" would otherwise fail and leave us in the wrong folder.
pushd "%~dp0" 2>nul
if errorlevel 1 (
    echo [ERROR] Cannot open the program folder:
    echo         %~dp0
    echo         Copy the folder to a local disk and try again.
    pause
    exit /b 1
)

chcp 65001 >nul 2>nul

where powershell >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Windows PowerShell was not found on this machine.
    echo         Please run launcher.ps1 manually.
    popd
    pause
    exit /b 1
)

if not exist "%~dp0launcher.ps1" (
    echo [ERROR] launcher.ps1 is missing next to Start.cmd.
    echo         The package is incomplete - extract the original zip again.
    popd
    pause
    exit /b 1
)

rem Files that arrive by zip / download carry a "downloaded from the internet"
rem mark that Windows SmartScreen and some antivirus products use to block
rem unsigned scripts. Clear it before PowerShell is started (best effort).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0.' -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue; Get-ChildItem -LiteralPath '%~dp0tools' -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>nul

rem -ExecutionPolicy Bypass covers the default (Restricted) policy of a fresh
rem Windows profile; -NoProfile keeps a broken user profile out of the way.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Startup failed. Exit code: %RC%
    echo         Check logs\startup.log and logs\setup.log for details.
    echo         If your organisation forces an AllSigned execution policy,
    echo         ask IT to allow scripts or run the app from a normal account.
    popd
    pause
    exit /b %RC%
)
popd
exit /b 0
