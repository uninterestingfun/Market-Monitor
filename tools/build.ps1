# Build Windows desktop packages: one-file exe + portable folder.
# Usage: powershell -ExecutionPolicy Bypass -File tools\build.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$pyi = Join-Path $root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $venvPy)) { throw "Missing .venv. Run: python -m venv .venv" }
if (-not (Test-Path $pyi)) { throw "Missing PyInstaller in .venv. Run: pip install pyinstaller" }

Write-Host "[1/4] make icon"
& $venvPy tools\make_icon.py

Write-Host "[2/4] clean old output"
foreach ($p in @("build\MarketMonitor", "build\MarketMonitor-portable", "dist\MarketMonitor-portable")) {
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

$argsCommon = @(
    "--noconfirm", "--clean", "--windowed",
    "--icon", "assets\icon.ico",
    "--add-data", "market_monitor\web;market_monitor\web",
    "--exclude-module", "matplotlib",
    "--exclude-module", "pandas",
    "--exclude-module", "PIL",
    "desktop.py"
)

Write-Host "[3/4] build one-file exe"
& $pyi --onefile --name MarketMonitor @argsCommon
if ($LASTEXITCODE -ne 0) { throw "one-file build failed" }

Write-Host "[4/4] build portable folder"
& $pyi --onedir --name MarketMonitor-portable @argsCommon
if ($LASTEXITCODE -ne 0) { throw "portable build failed" }

$zip = "dist\MarketMonitor-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "dist\MarketMonitor-portable\*" -DestinationPath $zip

$exeSize = (Get-Item "dist\MarketMonitor.exe").Length / 1MB
$zipSize = (Get-Item $zip).Length / 1MB
Write-Host ""
Write-Host "Done."
Write-Host ("  one-file : dist\MarketMonitor.exe  {0:N1} MB" -f $exeSize)
Write-Host ("  portable : {0}  {1:N1} MB" -f $zip, $zipSize)
