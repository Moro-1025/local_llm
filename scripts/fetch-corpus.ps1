<#
.SYNOPSIS
    RAG の検索対象コーパス(ai_harness の Markdown)を data/corpus/ai_harness/ に取得する。

.DESCRIPTION
    raw.githubusercontent.com から Markdown だけを個別に GET する(git clone はしない)。
    取得先は data/ 配下なので .gitignore 済み。既存ファイルは上書きする。

    ファイル一覧は ai_harness/docs/README.md(ドキュメントマップ)に載っている Markdown を
    固定で持つ。一覧を差し替えたいときは -ListFile に 1 行 1 パスのテキストを渡す。

.PARAMETER Ref
    取得するブランチ名またはコミット。既定は main。

.PARAMETER OutDir
    出力先ディレクトリ。既定は data\corpus\ai_harness。

.PARAMETER ListFile
    取得するパス一覧(リポジトリルートからの相対パス、1 行 1 パス)。省略時は組み込みの一覧。

.EXAMPLE
    .\scripts\fetch-corpus.ps1
    .\scripts\fetch-corpus.ps1 -Ref v1.2
#>
[CmdletBinding()]
param(
    [string]$Ref = "main",
    [string]$OutDir = "data\corpus\ai_harness",
    [string]$ListFile = ""
)

$ErrorActionPreference = "Stop"
$Repo = "Moro-1025/ai_harness"
$BaseUrl = "https://raw.githubusercontent.com/$Repo/$Ref"

# docs/README.md(ドキュメントマップ)に載っている Markdown。core/schemas/ は JSON なので含めない
$DefaultFiles = @(
    "README.md",
    "docs/README.md",
    "docs/core/principles.md",
    "docs/core/glossary.md",
    "docs/core/decision-model.md",
    "docs/learning/beginner/harness-basics.md",
    "docs/learning/beginner/simple-agent-tutorial.md",
    "docs/learning/beginner/coding-agent-tutorial.md",
    "docs/learning/intermediate/team-harness.md",
    "docs/learning/intermediate/agent-runtime.md",
    "docs/learning/intermediate/evidence-and-policy.md",
    "docs/learning/intermediate/ci-review-and-evaluation.md",
    "docs/reference/agent-development-handbook.md",
    "docs/reference/coding-agent-usage-handbook.md",
    "docs/profiles/personal-local.md",
    "docs/profiles/team-ci.md",
    "docs/profiles/production-high-risk.md",
    "docs/profiles/distributed-execution.md",
    "docs/profiles/codex.md"
)

if ($ListFile -ne "") {
    $Files = Get-Content -LiteralPath $ListFile -Encoding UTF8 | Where-Object { $_.Trim() -ne "" -and -not $_.Trim().StartsWith("#") }
} else {
    $Files = $DefaultFiles
}

$Root = Split-Path -Parent $PSScriptRoot
$Dest = Join-Path $Root $OutDir
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# PowerShell 5.1 は既定で TLS 1.0/1.1 を使おうとすることがあるので TLS 1.2 を明示する
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ok = 0
$failed = @()
foreach ($rel in $Files) {
    $rel = $rel.Trim()
    if (-not $rel.EndsWith(".md")) {
        Write-Warning "Markdown 以外はスキップ: $rel"
        continue
    }
    $url = "$BaseUrl/$rel"
    $target = Join-Path $Dest ($rel -replace "/", "\")
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    try {
        # -UseBasicParsing: IE エンジンに依存しない。-OutFile でそのまま保存し文字コードを変えない
        Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 60 -OutFile $target
        $ok++
        $size = (Get-Item -LiteralPath $target).Length
        Write-Host ("  ok   {0,-60} {1,8} bytes" -f $rel, $size)
    } catch {
        $failed += $rel
        Write-Warning "取得失敗: $url ($($_.Exception.Message))"
    }
}

Write-Host ""
Write-Host "取得先: $Dest"
Write-Host ("成功 {0} / 失敗 {1}" -f $ok, $failed.Count)
if ($failed.Count -gt 0) {
    $failed | ForEach-Object { Write-Host "  失敗: $_" }
    exit 1
}
