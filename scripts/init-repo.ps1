<#
.SYNOPSIS
  Phase 0 の初期化: 不要ファイルの退避 → git init → 最初のコミット。
  2 回実行しても壊れない(既に git 管理下なら init を飛ばす)。

.EXAMPLE
  .\scripts\init-repo.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git が見つかりません。https://git-scm.com/download/win からインストールしてください"
}

# 1. 展開済みの zip を親フォルダに退避(削除は手動で)
$archive = Join-Path $root "_archive"
New-Item -ItemType Directory -Force -Path $archive | Out-Null
Get-ChildItem -Path (Join-Path $root "llama-local") -Filter "*.zip" | ForEach-Object {
    Move-Item $_.FullName (Join-Path $archive $_.Name) -Force
    Write-Host "退避: $($_.Name) -> _archive\"
}
# 古い起動ログも logs/ に移す(logs/ は .gitignore 済み)
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
Get-ChildItem -Path $root -Filter "*.log" | ForEach-Object {
    Move-Item $_.FullName (Join-Path $root "logs\$($_.Name)") -Force
    Write-Host "移動: $($_.Name) -> logs\"
}

# 2. git 初期化
if (-not (Test-Path (Join-Path $root ".git"))) {
    git init -b main | Out-Null
    Write-Host "git init 完了 (branch: main)"
} else {
    Write-Host "既に git 管理下です"
}

# 3. 管理対象の確認とコミット
Write-Host ""
Write-Host "git に入るファイル(llama-local/ や *.log が含まれていないことを確認):"
git add -A
git status --short
Write-Host ""

$tracked = git status --short
if ($tracked) {
    git commit -m "Phase 0: README, .gitignore, 起動・計測スクリプト" | Out-Null
    Write-Host "コミットしました:"
    git log --oneline -1
} else {
    Write-Host "コミットするものはありません"
}

Write-Host ""
Write-Host "_archive\ の zip は不要なら削除してください(.gitignore で除外済み)。"
