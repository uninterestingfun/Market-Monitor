@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   提交并推送到远端
echo   用法: push.bat "feat: 说明"    （不填则用时间戳）
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\push.ps1" -Message "%~1"
if errorlevel 1 (
    echo.
    echo [失败] 推送未完成，请查看上面的日志
    pause
    exit /b 1
)

pause
