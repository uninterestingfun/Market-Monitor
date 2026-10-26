# Build Windows desktop package (portable folder) and the single-file installer.
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

Write-Host "[1/4] make icon"
& $venvPy tools\make_icon.py

Write-Host "[2/4] clean old output"
foreach ($p in @("build\MarketMonitor", "dist\MarketMonitor", "dist\MarketMonitor-portable.zip",
                 "dist\MarketMonitor-Setup.exe", "installer\payload.zip")) {
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

# build info: used by the in-app updater to compare with the remote commit
$sha = ""
try { $sha = (git rev-parse --short HEAD).Trim() } catch { $sha = "" }
if (-not $sha) { $sha = "unknown" }
$built = Get-Date -Format "yyyy-MM-dd HH:mm"
$ver = ""
try { $ver = (Select-String -Path "market_monitor\version.py" -Pattern '^__version__ = "(.+)"').Matches[0].Groups[1].Value } catch { $ver = "0.0.0" }
Set-Content -Path "market_monitor\_buildinfo.py" -Encoding UTF8 -Value @"
BUILD_SHA = "$sha"
BUILT_AT = "$built"
BUILD_VERSION = "$ver"
"@
Write-Host "      build info: v$ver / $sha / $built"


$argsCommon = @(
    "--noconfirm", "--clean", "--windowed",
    "--icon", "assets\icon.ico",
    "--add-data", "market_monitor\web;market_monitor\web",
    "--add-data", "market_monitor\assets;market_monitor\assets",
    "--exclude-module", "matplotlib",
    "--exclude-module", "pandas",
    "--exclude-module", "PIL",
    "desktop.py"
)

Write-Host "[3/4] build portable folder"
# onedir only: the exe never extracts to %TEMP%, and data lives in data\ next to the exe.
& $pyi --onedir --name MarketMonitor @argsCommon
if ($LASTEXITCODE -ne 0) { throw "portable build failed" }

$zip = "dist\MarketMonitor-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "dist\MarketMonitor" -DestinationPath $zip

Write-Host "[4/4] build installer (native C#)"
# The installer is a plain .NET WinForms exe with payload.zip embedded as a resource:
# no Python runtime, no self-extraction to %TEMP%.
Compress-Archive -Path "dist\MarketMonitor\*" -DestinationPath "installer\payload.zip" -Force
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "csc.exe not found (.NET Framework 4.x required)" }
& $csc /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 `
    "/win32icon:assets\icon.ico" "/resource:installer\payload.zip,payload.zip" `
    "/r:System.dll" "/r:System.Core.dll" "/r:System.Drawing.dll" "/r:System.Windows.Forms.dll" `
    "/r:System.IO.Compression.dll" "/r:System.IO.Compression.FileSystem.dll" "/r:Microsoft.CSharp.dll" `
    "/out:dist\MarketMonitor-Setup.exe" "installer\Installer.cs"
if ($LASTEXITCODE -ne 0) { throw "installer build failed" }

$exeSize = (Get-Item "dist\MarketMonitor\MarketMonitor.exe").Length / 1MB
$zipSize = (Get-Item $zip).Length / 1MB
$setupSize = (Get-Item "dist\MarketMonitor-Setup.exe").Length / 1MB
Write-Host ""
Write-Host "Done."
Write-Host ("  exe        : dist\MarketMonitor\MarketMonitor.exe  {0:N1} MB" -f $exeSize)
Write-Host ("  portable   : {0}  {1:N1} MB" -f $zip, $zipSize)
Write-Host ("  installer  : dist\MarketMonitor-Setup.exe  {0:N1} MB" -f $setupSize)
