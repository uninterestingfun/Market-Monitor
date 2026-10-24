@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   更新 release 目录（复制 dist 产物 + 重算 SHA256）
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\release.ps1"
if errorlevel 1 (
    echo.
    echo [失败] release 更新失败，请查看上面的日志
    pause
    exit /b 1
)

pause
