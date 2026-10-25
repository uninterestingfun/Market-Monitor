@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   Commit and push to remote
echo   Usage: push.bat "feat: message"   (timestamp if omitted)
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\push.ps1" -Message "%~1"
if errorlevel 1 (
    echo.
    echo [FAILED] push did not complete, see the log above
    pause
    exit /b 1
)

pause
