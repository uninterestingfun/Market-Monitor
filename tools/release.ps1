# 把 dist\ 里的产物同步到 release\ 并更新校验值（不提交、不推送）
# 默认只同步安装包；加 -WithPortable 同时同步便携包
param(
    [switch]$WithPortable
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$setup = "dist\MarketMonitor-Setup.exe"
if (-not (Test-Path $setup)) {
    Write-Host "[release] dist\MarketMonitor-Setup.exe 不存在，请先运行 build.bat" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path "release" | Out-Null
Copy-Item $setup "release\MarketMonitor-Setup.exe" -Force

if ($WithPortable) {
    $portable = "dist\MarketMonitor-portable.zip"
    if (Test-Path $portable) {
        Copy-Item $portable "release\MarketMonitor-portable.zip" -Force
        Write-Host "[release] 已同步便携包" -ForegroundColor Cyan
    }
}

$file = Get-Item "release\MarketMonitor-Setup.exe"
$hash = (Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLower()
$size = [math]::Round($file.Length / 1MB, 1)

$readme = "release\README.md"
if (Test-Path $readme) {
    $lines = Get-Content $readme -Encoding UTF8
    $updated = foreach ($line in $lines) {
        if ($line -match '^\|\s*`?MarketMonitor-Setup\.exe') {
            '| `MarketMonitor-Setup.exe` | Windows 安装包（{0} MB） | `{1}` |' -f $size, $hash
        } elseif ($line -match '^版本：') {
            "版本：$ver · 构建于 " + (Get-Date -Format "yyyy-MM-dd")
        } else {
            $line
        }
    }
    $updated | Set-Content $readme -Encoding UTF8
}

Write-Host ""
Write-Host "[release] 已同步" -ForegroundColor Green
Write-Host "  文件: release\MarketMonitor-Setup.exe ($size MB)"
Write-Host "  SHA256: $hash"
if (-not $WithPortable) {
    Write-Host "  提示: 便携包未同步，如需请运行 release.bat -WithPortable" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "下一步: 运行 push.bat 提交并推送到远端"
