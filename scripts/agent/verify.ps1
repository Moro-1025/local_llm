<#
.SYNOPSIS
  検証の単一入口。ai_harness の coding-agent-tutorial にある scripts/agent/verify に相当する。
  実装の前(Baseline)と後(Targeted Verification)に同じコマンドを打ち、出力を Completion Report に残す。

.DESCRIPTION
  実行する項目:
    1. Task Contract / Completion Decision の JSON Schema 検証(scripts/agent/check_contracts.py)
    2. git diff --check(空白の混入)
    3. pytest(-Scope でディレクトリを絞れる)
    4. -Agent 指定時: agent/harness の vitest(Phase 4 以降)
    5. -Training 指定時: training/ のテスト(Phase 5 以降、WSL で実行)
  どれかが失敗したら終了コード 1。失敗を成功扱いしない。

.EXAMPLE
  .\scripts\agent\verify.ps1                # 全部
  .\scripts\agent\verify.ps1 -Scope tests\rag
  .\scripts\agent\verify.ps1 -Agent
#>
[CmdletBinding()]
param(
    [string]$Scope = "",
    [switch]$Agent,
    [switch]$Training,
    [switch]$SkipPytest
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
$failed = @()

function Step([string]$name, [scriptblock]$body) {
    Write-Host ""
    Write-Host "=== $name ===" -ForegroundColor Cyan
    & $body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "--> NG: $name (exit $LASTEXITCODE)" -ForegroundColor Red
        $script:failed += $name
    } else {
        Write-Host "--> OK: $name" -ForegroundColor Green
    }
}

Step "contracts schema" { uv run python scripts/agent/check_contracts.py }
Step "eval results schema" { uv run python scripts/agent/check_eval_results.py }
Step "git diff --check" { git diff --check; if ($LASTEXITCODE -eq $null) { $global:LASTEXITCODE = 0 } }

if (-not $SkipPytest) {
    if ($Scope) { Step "pytest $Scope" { uv run pytest -q $Scope } }
    else        { Step "pytest"        { uv run pytest -q } }
}

if ($Agent) {
    if (Test-Path (Join-Path $root "agent\harness\package.json")) {
        Step "vitest (agent/harness)" { Push-Location agent\harness; npm test --silent; Pop-Location }
    } else {
        Write-Host "agent/harness が無いためスキップ" -ForegroundColor Yellow
    }
}

if ($Training) {
    if (Test-Path (Join-Path $root "training\run_tests.sh")) {
        # worktree でも動くように、リポジトリのパスを WSL 側のパスに変換して渡す
        # バックスラッシュは WSL 側の bash に食われるので / に直してから渡す
        $wslRoot = (wsl -d Ubuntu -- wslpath -u ($root -replace '\\', '/')).Trim()
        Step "training tests (WSL)" { wsl -d Ubuntu -- bash -lc "cd '$wslRoot/training' && ./run_tests.sh" }
    } else {
        Write-Host "training/run_tests.sh が無いためスキップ" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host ("commit: " + (git rev-parse --short HEAD)) -ForegroundColor DarkGray
if ($failed.Count -gt 0) {
    Write-Host ("FAILED: " + ($failed -join ", ")) -ForegroundColor Red
    exit 1
}
Write-Host "ALL OK" -ForegroundColor Green
exit 0
