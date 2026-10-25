<#
.SYNOPSIS
  このリポジトリ自身の Markdown(README.md と docs/**/*.md)を RAG のコーパス data/corpus/local_llm/ にコピーする。

.DESCRIPTION
  Phase 6 の評価データ(eval/datasets/qa-v1.jsonl)は docs/ と ai_harness の両方から合成されているので、
  RAG あり構成(B / E)の索引にも両方を入れる。ai_harness 側は scripts/fetch-corpus.ps1 で取得する。
  コピー先は data/ 配下なので git 管理外。既存ファイルは上書きし、除外パターンに当たるものは消す。

  除外: docs/journal/phase-6.md と docs/harness/reports/phase-6* (評価結果そのものを索引に入れないため)。

.EXAMPLE
  .\scripts\sync-local-corpus.ps1
  uv run llm-index          # その後に索引を更新する
#>
[CmdletBinding()]
param(
    [string]$OutDir = "data\corpus\local_llm"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $root $OutDir
$exclude = @("docs\journal\phase-6.md", "docs\harness\reports\phase-6")

$files = @(Get-Item (Join-Path $root "README.md"))
$files += Get-ChildItem -Path (Join-Path $root "docs") -Recurse -Filter "*.md"

$copied = 0
foreach ($f in $files) {
    $rel = $f.FullName.Substring($root.Length + 1)
    $skip = $false
    foreach ($ex in $exclude) { if ($rel.StartsWith($ex)) { $skip = $true } }
    if ($skip) { Write-Host "  skip  $rel"; continue }
    $target = Join-Path $dest $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $f.FullName -Destination $target -Force
    $copied++
}
Write-Host "コピー: $copied ファイル -> $dest"
Write-Host "次に 'uv run llm-index' で索引を更新してください。"
