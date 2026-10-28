@echo off
rem Register (or refresh) the daily report scheduled task.
rem Default: every day at 16:30 local time. Pass a time as the first argument to change it.
setlocal
set TASK=MarketMonitor-DailyReport
set TIME=%1
if "%TIME%"=="" set TIME=16:30

set SCRIPT=%~dp0report-job.bat
if not exist "%SCRIPT%" (
    echo [task] missing %SCRIPT%
    exit /b 1
)

schtasks /Create /TN "%TASK%" /TR "\"%SCRIPT%\"" /SC DAILY /ST %TIME% /F >nul
if errorlevel 1 (
    echo [task] create failed
    exit /b 1
)
echo [task] registered: %TASK% daily at %TIME%
echo [task] script: %SCRIPT%
schtasks /Query /TN "%TASK%" /FO LIST | findstr /i "TaskName Next Status"
