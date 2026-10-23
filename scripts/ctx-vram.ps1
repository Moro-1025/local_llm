<#
.SYNOPSIS
  ctx-size(コンテキスト長)を変えながら llama-server を起動し、
  nvidia-smi で VRAM 使用量を記録する。KV キャッシュがどれだけ VRAM を食うかを体感する用。

.DESCRIPTION
  各 ctx-size ごとに:
    1. llama-server を単体モデルモード(-hf)で一時ポートに起動
    2. /health が ok を返すまで待つ(モデル読み込み完了)
    3. nvidia-smi で VRAM 使用量を取得
    4. サーバを停止
  結果は bench/ctx-vram-<日時>.md に保存する。

.EXAMPLE
  .\scripts\ctx-vram.ps1
  .\scripts\ctx-vram.ps1 -Model "unsloth/Qwen3-8B-GGUF:Q4_K_M" -CtxSizes 4096,8192,16384,32768,65536
#>
[CmdletBinding()]
param(
    [string]$Model = "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M",
    [int[]]$CtxSizes = @(8192, 16384, 32768),
    [int]$Port = 8089,
    [int]$TimeoutSec = 300,
    [string[]]$ExtraArgs = @(),
    [string]$LlamaDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $LlamaDir) { $LlamaDir = Join-Path $root "llama-local" }
$exe = Join-Path $LlamaDir "llama-server.exe"
if (-not (Test-Path $exe)) { throw "llama-server.exe が見つかりません: $exe" }

$outDir = Join-Path $root "bench"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outFile = Join-Path $outDir "ctx-vram-$stamp.md"

function Get-VramUsedMiB {
    $v = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null
    if ($v) { return [int]($v -split "`n")[0].Trim() } else { return -1 }
}

# MoE はエキスパートを CPU に置く(models.ini と同じ)
if ($Model -match "A3B|A22B|MoE|-A\d+B" -and ($ExtraArgs -notcontains "--cpu-moe")) {
    $ExtraArgs += "--cpu-moe"
}

$idle = Get-VramUsedMiB
$rows = @()
Write-Host "モデル: $Model"
Write-Host "起動前の VRAM 使用量: $idle MiB"

foreach ($ctx in $CtxSizes) {
    Write-Host ""
    Write-Host "=== ctx-size = $ctx ==="
    $cliArgs = @("-hf", $Model, "-c", $ctx, "--port", $Port, "--host", "127.0.0.1", "--no-webui", "-ngl", "auto", "-fa", "auto") + $ExtraArgs
    $p = Start-Process -FilePath $exe -ArgumentList $cliArgs -PassThru -WindowStyle Hidden `
        -RedirectStandardError (Join-Path $outDir "ctx-$ctx-$stamp.log")

    $ok = $false
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if ($p.HasExited) { break }
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            if ($h.status -eq "ok") { $ok = $true; break }
        } catch { }
        Start-Sleep -Seconds 2
    }

    if ($ok) {
        Start-Sleep -Seconds 3
        $used = Get-VramUsedMiB
        $delta = $used - $idle
        Write-Host ("  読み込み完了 {0:N0} 秒, VRAM 使用 {1} MiB (起動前比 +{2} MiB)" -f $sw.Elapsed.TotalSeconds, $used, $delta)
        $rows += "| $ctx | $used | +$delta | {0:N0} |" -f $sw.Elapsed.TotalSeconds
    } else {
        $why = if ($p.HasExited) { "起動失敗(ログ参照: bench/ctx-$ctx-$stamp.log)" } else { "タイムアウト" }
        Write-Warning "  $why"
        $rows += "| $ctx | - | - | $why |"
    }

    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
    Start-Sleep -Seconds 3
}

@(
    "# ctx-size と VRAM 使用量 ($stamp)",
    "",
    "- モデル: $Model",
    "- 追加引数: $($ExtraArgs -join ' ')",
    "- 起動前の VRAM 使用量: $idle MiB",
    "",
    "| ctx-size | VRAM 使用 (MiB) | 起動前比 | 読み込み秒 |",
    "| --- | --- | --- | --- |"
) + $rows | Set-Content -Encoding UTF8 $outFile

Write-Host ""
Write-Host "保存しました: $outFile"
