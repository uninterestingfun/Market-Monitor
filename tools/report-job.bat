@echo off
rem Generate the daily market report and push it to the remote repo.
rem Registered as a Windows scheduled task (see tools\install-report-task.bat).
setlocal
cd /d "%~dp0.."
set GIT_SSH=C:\Windows\System32\OpenSSH\ssh.exe
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if not exist "data" mkdir "data" >nul 2>&1
if not exist ".venv\Scripts\python.exe" (
    echo [report] .venv not found ? run from the project root >> "data\report-job.log"
    exit /b 1
)

echo [report] %DATE% %TIME% start >> "data\report-job.log"
".venv\Scripts\python.exe" run.py report --push --wait 150 >> "data\report-job.log" 2>&1
set RC=%ERRORLEVEL%
echo [report] %DATE% %TIME% exit=%RC% >> "data\report-job.log"
exit /b %RC%
