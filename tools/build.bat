@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   Market Monitor  -  Windows build
echo   Output: dist\MarketMonitor\  dist\MarketMonitor-portable.zip
echo           dist\MarketMonitor-Setup.exe
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found in PATH
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\build.ps1"
if errorlevel 1 (
    echo.
    echo [FAILED] build did not complete, see the log above
    pause
    exit /b 1
)

echo.
echo [OK] installer : dist\MarketMonitor-Setup.exe
echo      portable  : dist\MarketMonitor-portable.zip
pause
