<#
.SYNOPSIS
  5 構成(A〜E)の評価を順番に実行し、最後に比較表を出す(Phase 6 / EVAL-002)。

.DESCRIPTION
  uv run llm-eval --suite qa --config X を A〜E の順に呼ぶ。モデルは llm-eval がルーターモードの
  /models/load, /models/unload で 1 つずつ載せ替えるので、12 GB VRAM でも順に通る。
  1 構成でも失敗したら終了コード 1(失敗を成功扱いしない)。途中経過は eval/results/*.partial.json。

.EXAMPLE
  .\scripts\run-eval-all.ps1                                   # 全件(147 問)
  .\scripts\run-eval-all.ps1 -BaseUrl http://127.0.0.1:8082     # 別ポートの llama-server
  .\scripts\run-eval-all.ps1 -N 40 -Seed 7 -Configs A,C         # 部分集合・一部の構成
#>
[CmdletBinding()]
param(
    [string[]]$Configs = @("A", "B", "C", "D", "E"),
    [int]$N = 0,
    [int]$Seed = 42,
    [string]$BaseUrl = "",
    [string]$GraderModel = "",
    [string]$Suite = "qa"
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$failed = @()

foreach ($c in $Configs) {
    $cliArgs = @("run", "llm-eval", "--suite", $Suite, "--config", $c, "--seed", $Seed)
    if ($N -gt 0)       { $cliArgs += @("--n", $N) }
    if ($BaseUrl)       { $cliArgs += @("--base-url", $BaseUrl) }
    if ($GraderModel)   { $cliArgs += @("--grader-model", $GraderModel) }
    Write-Host ""
    Write-Host "=== 構成 $c === $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
    & uv @cliArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "--> NG: 構成 $c (exit $LASTEXITCODE)" -ForegroundColor Red
        $failed += $c
    }
}

Write-Host ""
Write-Host "=== 比較表(README に転記) ===" -ForegroundColor Cyan
& uv run llm-eval --summarize --suite $Suite
if ($failed.Count -gt 0) {
    Write-Host ("FAILED: " + ($failed -join ", ")) -ForegroundColor Red
    exit 1
}
Write-Host "ALL OK" -ForegroundColor Green
exit 0
