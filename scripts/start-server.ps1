<#
.SYNOPSIS
  llama-server をルーターモード(models.ini のプリセット)で起動する。

.EXAMPLE
  .\scripts\start-server.ps1
  .\scripts\start-server.ps1 -Port 8081 -Preset .\models.ini
  .\scripts\start-server.ps1 -Expose      # LAN 内の他端末からも接続できるようにする
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [string]$Preset = "",
    [switch]$Expose,
    [string]$LlamaDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not $LlamaDir) { $LlamaDir = Join-Path $root "llama-local" }
if (-not $Preset)   { $Preset   = Join-Path $root "models.ini" }

$exe = Join-Path $LlamaDir "llama-server.exe"
if (-not (Test-Path $exe))    { throw "llama-server.exe が見つかりません: $exe (README のセットアップ手順を参照)" }
if (-not (Test-Path $Preset)) { throw "models.ini が見つかりません: $Preset" }

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("server-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

$bindHost = if ($Expose) { "0.0.0.0" } else { "127.0.0.1" }

$cliArgs = @(
    "--models-preset", $Preset,
    "--host", $bindHost,
    "--port", $Port,
    "--log-file", $logFile
)

Write-Host "llama-server を起動します"
Write-Host "  preset : $Preset"
Write-Host "  URL    : http://$bindHost`:$Port  (Web UI も同じ URL)"
Write-Host "  log    : $logFile"
Write-Host "  停止   : Ctrl+C"
Write-Host ""

& $exe @cliArgs
