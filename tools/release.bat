@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   Update release folder (copy dist + recompute SHA256)
echo   Usage: release.bat                setup only
echo          release.bat -WithPortable  setup + portable zip
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\release.ps1" %*
if errorlevel 1 (
    echo.
    echo [FAILED] release update failed, see the log above
    pause
    exit /b 1
)

pause
