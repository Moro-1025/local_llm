<#
.SYNOPSIS
  llama-bench で複数モデルの速度(prompt 処理 pp / 生成 tg)を計測し、
  bench/ 以下に Markdown で保存する。

.DESCRIPTION
  モデルは Hugging Face のキャッシュ(%USERPROFILE%\.cache\huggingface\hub)から探す。
  まだダウンロードしていないモデルはスキップするので、先に llama-server の Web UI で
  一度そのモデルを選んで会話し、ダウンロードを済ませておくこと。

.EXAMPLE
  .\scripts\bench.ps1
  .\scripts\bench.ps1 -Models "unsloth/Qwen3-8B-GGUF:Q4_K_M"
#>
[CmdletBinding()]
param(
    # "org/repo:QUANT" の形式。models.ini のセクション名と同じ。
    [string[]]$Models = @(
        "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M",
        "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M",
        "unsloth/Qwen3-8B-GGUF:Q4_K_M"
    ),
    [int]$PromptTokens = 512,
    [int]$GenTokens = 128,
    [int]$Repeat = 3,
    # VRAM に収まらない dense モデルは GPU に載せる層数を指定する(未指定は全層 = 99)。
    # 27B の 39 は llama-server の自動フィット結果(llama-startup.log)から。
    [hashtable]$GpuLayers = @{ "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_M" = 39 },
    [string]$LlamaDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $LlamaDir) { $LlamaDir = Join-Path $root "llama-local" }
$bench = Join-Path $LlamaDir "llama-bench.exe"
if (-not (Test-Path $bench)) { throw "llama-bench.exe が見つかりません: $bench" }

$hubDir = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
$outDir = Join-Path $root "bench"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outFile = Join-Path $outDir "bench-$stamp.md"

function Find-Gguf([string]$spec) {
    $repo, $quant = $spec -split ":", 2
    $folder = "models--" + ($repo -replace "/", "--")
    $snap = Join-Path $hubDir "$folder\snapshots"
    if (-not (Test-Path $snap)) { return $null }
    $files = Get-ChildItem -Path $snap -Recurse -Filter "*.gguf" |
        Where-Object { -not $quant -or $_.Name -like "*$quant*" } |
        Sort-Object Name
    if (-not $files) { return $null }
    # 分割 GGUF は 00001-of-0000N を渡せば残りも読み込まれる
    $first = $files | Where-Object { $_.Name -match "00001-of-" } | Select-Object -First 1
    if ($first) { return $first.FullName }
    return $files[0].FullName
}

$gpu = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null) -join ""
@(
    "# llama-bench 結果 ($stamp)",
    "",
    "- GPU: $gpu",
    "- 条件: pp=$PromptTokens, tg=$GenTokens, repeat=$Repeat, flash-attn=on",
    "- pp = プロンプト処理速度 (tok/s), tg = 生成速度 (tok/s)",
    ""
) | Set-Content -Encoding UTF8 $outFile

foreach ($spec in $Models) {
    $path = Find-Gguf $spec
    Write-Host ""
    Write-Host "=== $spec ==="
    if (-not $path) {
        Write-Warning "未ダウンロードのためスキップ: $spec (先に Web UI でこのモデルを一度使ってください)"
        Add-Content -Encoding UTF8 $outFile "## $spec`n`n未ダウンロードのためスキップ`n"
        continue
    }
    Write-Host "  gguf: $path"

    $ngl = if ($GpuLayers.ContainsKey($spec)) { $GpuLayers[$spec] } else { 99 }
    Write-Host "  GPU 層数 (-ngl): $ngl"
    $cliArgs = @("-m", $path, "-p", $PromptTokens, "-n", $GenTokens, "-r", $Repeat, "-fa", "1", "-ngl", $ngl, "-o", "md")
    # MoE モデルはエキスパート層を CPU に置く(models.ini の cpu-moe = true と同じ意味)
    if ($spec -match "A3B|A22B|MoE|-A\d+B") {
        $cliArgs += @("-ot", "\.ffn_.*_exps\.=CPU")
        Write-Host "  MoE と判定: エキスパートを CPU に配置 (-ot)"
    }

    $result = & $bench @cliArgs 2>&1 | Out-String
    Write-Host $result
    Add-Content -Encoding UTF8 $outFile "## $spec`n`n$result`n"
}

Write-Host ""
Write-Host "保存しました: $outFile"
Write-Host "この表を README.md の「ベンチマーク」節に貼り付けてください。"
