# Build Windows desktop package (portable folder, no temp extraction).
# Usage: powershell -ExecutionPolicy Bypass -File tools\build.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
$pyi = Join-Path $root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $venvPy)) { throw "Missing .venv. Run: python -m venv .venv" }
if (-not (Test-Path $pyi)) { throw "Missing PyInstaller in .venv. Run: pip install pyinstaller" }
& $venvPy -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) { throw "Missing Pillow in .venv. Run: pip install pillow" }

Write-Host "[1/3] make icon"
& $venvPy tools\make_icon.py

Write-Host "[2/3] clean old output"
foreach ($p in @("build\MarketMonitor", "dist\MarketMonitor", "dist\MarketMonitor-portable.zip", "dist\MarketMonitor.exe")) {
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

Write-Host "[3/3] build portable folder"
# onedir only: the exe never extracts to %TEMP%, and data lives in data\ next to the exe.
# The one-file mode must extract to a temp dir, which fails on locked-down machines.
& $pyi --onedir --name MarketMonitor @argsCommon
if ($LASTEXITCODE -ne 0) { throw "portable build failed" }

$zip = "dist\MarketMonitor-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "dist\MarketMonitor" -DestinationPath $zip

$exeSize = (Get-Item "dist\MarketMonitor\MarketMonitor.exe").Length / 1MB
$zipSize = (Get-Item $zip).Length / 1MB
Write-Host ""
Write-Host "Done."
Write-Host ("  exe     : dist\MarketMonitor\MarketMonitor.exe  {0:N1} MB" -f $exeSize)
Write-Host ("  package : {0}  {1:N1} MB" -f $zip, $zipSize)
