#!/usr/bin/env pwsh
# -----------------------------------------------------------------------------
#  Nature AI Studio launcher (Windows)
#
#  Starts the FastAPI dashboard on 127.0.0.1 and opens a Cloudflare quick
#  tunnel, then prints the public https://*.trycloudflare.com URL.
#
#    .\run.ps1                 # normal start
#    .\run.ps1 -Port 9000      # different local port
#    .\run.ps1 -NoTunnel       # local only
#    .\run.ps1 -TunnelOnly     # reuse an already-running server
# -----------------------------------------------------------------------------
param(
    [int]$Port = 0,
    [switch]$NoTunnel,
    [switch]$TunnelOnly
)

$ErrorActionPreference = 'Stop'
$Studio = $PSScriptRoot
Set-Location $Studio

# -- config ----------------------------------------------------------------
$envFile = Join-Path (Split-Path $Studio -Parent) '.env'
if (-not (Test-Path $envFile)) {
    Write-Host "x Missing .env at $envFile - copy .env.example and fill in KILOCODE_API_KEY." -ForegroundColor Red
    exit 1
}
$envText = Get-Content $envFile -Raw
if ($envText -notmatch '(?m)^[ \t]*STUDIO_PASSWORD[ \t]*=[ \t]*(\S.*)$') {
    $alphabet = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#%_-'
    $pw = -join ((1..18) | ForEach-Object { $alphabet[(Get-Random -Maximum $alphabet.Length)] })
    Add-Content $envFile "`n# -- Nature AI Studio dashboard --`nSTUDIO_PASSWORD=$pw"
    Write-Host "Generated a dashboard password:`n    $pw`n" -ForegroundColor Yellow
    Write-Host "Change it any time in $envFile`n" -ForegroundColor DarkGray
}

# Port precedence: -Port switch, then STUDIO_PORT in .env, then 8787.
if ($Port -le 0) {
    $m = [regex]::Match((Get-Content $envFile -Raw), '(?m)^[ \t]*STUDIO_PORT[ \t]*=[ \t]*(\d+)')
    $parsed = if ($m.Success) { [int]$m.Groups[1].Value } else { 0 }
    $Port = if ($parsed -gt 0) { $parsed } else { 8787 }
}
$env:STUDIO_PORT = "$Port"

$UrlFile = Join-Path $Studio 'public_url.txt'
$LogDir  = Join-Path $Studio 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Find-Cloudflared {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
            "$env:ProgramFiles\cloudflared\cloudflared.exe",
            "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
            "$env:LOCALAPPDATA\Microsoft\WinGet\Links\cloudflared.exe")) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

# -- 1. API server ---------------------------------------------------------
$api = $null
if (-not $TunnelOnly) {
    Write-Host "> starting dashboard on 127.0.0.1:$Port" -ForegroundColor Cyan
    $api = Start-Process python `
        -ArgumentList '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', "$Port", '--log-level', 'info' `
        -WorkingDirectory $Studio -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir 'api.out.log') `
        -RedirectStandardError  (Join-Path $LogDir 'api.err.log')

    $ready = $false
    foreach ($i in 1..40) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/session" -UseBasicParsing -TimeoutSec 3 | Out-Null
            $ready = $true; break
        } catch { }
    }
    if (-not $ready) {
        Write-Host "x The API did not come up. See $LogDir\api.err.log" -ForegroundColor Red
        if ($api) { Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue }
        exit 1
    }
    Write-Host "v dashboard is live on http://127.0.0.1:$Port" -ForegroundColor Green
}

if ($NoTunnel) {
    Write-Host "`nLocal only. Open http://127.0.0.1:$Port`n" -ForegroundColor Yellow
    if ($api) { Wait-Process -Id $api.Id }
    exit 0
}

# -- 2. Cloudflare quick tunnel --------------------------------------------
$cf = Find-Cloudflared
if (-not $cf) {
    Write-Host "x cloudflared not found. Install it with:" -ForegroundColor Red
    Write-Host "    winget install --id Cloudflare.cloudflared" -ForegroundColor Red
    if ($api) { Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}

Write-Host "> opening Cloudflare quick tunnel..." -ForegroundColor Cyan
Remove-Item $UrlFile -ErrorAction SilentlyContinue
$cfLog = Join-Path $LogDir 'cloudflared.log'
$tunnel = Start-Process $cf `
    -ArgumentList 'tunnel', '--no-autoupdate', '--url', "http://127.0.0.1:$Port" `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LogDir 'cf.out.log') `
    -RedirectStandardError $cfLog

$public = $null
foreach ($i in 1..60) {
    Start-Sleep -Milliseconds 500
    if (Test-Path $cfLog) {
        $hit = Select-String -Path $cfLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches |
               Select-Object -First 1
        if ($hit) {
            $public = [regex]::Match($hit.Line, 'https://[a-z0-9-]+\.trycloudflare\.com').Value
            [System.IO.File]::WriteAllText($UrlFile, $public, (New-Object System.Text.UTF8Encoding($false)))
            break
        }
    }
    if ($tunnel.HasExited) { break }
}

Write-Host ""
if ($public) {
    Write-Host "==========================================================" -ForegroundColor DarkGreen
    Write-Host "  PUBLIC URL   $public" -ForegroundColor Green
    Write-Host "  (shown in the dashboard header - click to copy)" -ForegroundColor DarkGray
    Write-Host "==========================================================" -ForegroundColor DarkGreen
    Write-Host ""
} else {
    Write-Host "! Tunnel URL not found yet - see $cfLog" -ForegroundColor Yellow
}
Write-Host "Keep this window open. Ctrl+C stops the tunnel and the server." -ForegroundColor DarkGray

# -- 3. supervise ----------------------------------------------------------
try {
    while ($true) {
        Start-Sleep -Seconds 5
        if ($api -and $api.HasExited) { Write-Host "x API server exited." -ForegroundColor Red; break }
        if ($tunnel.HasExited) { Write-Host "x Cloudflare tunnel exited - see $cfLog" -ForegroundColor Red; break }
    }
} finally {
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    if ($api) { Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item $UrlFile -ErrorAction SilentlyContinue
}