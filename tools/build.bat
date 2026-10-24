@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   Market Monitor  -  Windows 打包
echo   产物: dist\MarketMonitor\  dist\MarketMonitor-portable.zip
echo         dist\MarketMonitor-Setup.exe
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先把 Python 加入 PATH
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\build.ps1"
if errorlevel 1 (
    echo.
    echo [失败] 打包未完成，请查看上面的日志
    pause
    exit /b 1
)

echo.
echo [完成] 安装包: dist\MarketMonitor-Setup.exe
echo       便携包: dist\MarketMonitor-portable.zip
pause
