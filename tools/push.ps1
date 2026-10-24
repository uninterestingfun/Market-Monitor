# 提交当前改动并推送到远端（未指定说明时用时间戳）
param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:GIT_TERMINAL_PROMPT = "0"

if (-not (Test-Path ".git")) {
    Write-Host "[push] 当前目录不是 git 仓库" -ForegroundColor Red
    exit 1
}

$status = git status --porcelain
if (-not $status) {
    Write-Host "[push] 没有需要提交的改动，直接推送…" -ForegroundColor Yellow
} else {
    if (-not $Message) {
        $Message = "chore: update " + (Get-Date -Format "yyyy-MM-dd HH:mm")
    }
    Write-Host "[push] 本次改动：" -ForegroundColor Cyan
    git status --short
    git add -A
    git commit -m $Message | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[push] 提交失败" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
git push | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host "[push] 推送失败（检查 SSH key 或网络）" -ForegroundColor Red
    exit 1
}

$head = (git log --oneline -1)
Write-Host ""
Write-Host "[push] 完成: $head" -ForegroundColor Green
