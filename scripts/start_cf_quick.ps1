param(
    [string]$PidFile,
    [int]$Port = 8768,
    [string]$LogFile
)

# Quick Tunnel launcher — the remote.quick_tunnel=true branch of
# scripts/start_pan.bat. Runs:
#
#   cloudflared tunnel --url http://127.0.0.1:<port>
#
# No config.yml is involved; Cloudflare assigns a temporary, random
# trycloudflare.com URL (unstable across restarts). cloudflared prints the
# URL to its log, so --logfile keeps it retrievable and also puts a unique
# pan_cf_quick_<port>.log marker on the process command line — the same
# trick start_cf.ps1 uses with its pan_cf_config_<port>.yml temp config —
# so stop_pan.bat can match this process safely.

$cmd = Get-Command 'cloudflared.exe' -ErrorAction SilentlyContinue
if (-not $cmd) {
    Write-Host '[Remote] cloudflared not found in PATH; quick tunnel not started.'
    exit 1
}

if (-not $LogFile) {
    $base = Split-Path $PSScriptRoot -Parent
    $LogFile = Join-Path $base ("data\logs\pan_cf_quick_{0}.log" -f $Port)
}
$logDir = Split-Path -Parent $LogFile
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
# Start from a clean log so URL extraction never picks up a stale URL.
Remove-Item -LiteralPath $LogFile -Force -ErrorAction SilentlyContinue

try {
    $p = Start-Process -FilePath $cmd.Source `
        -ArgumentList @('tunnel', '--url', "http://127.0.0.1:$Port", '--logfile', $LogFile) `
        -WindowStyle Minimized -PassThru
} catch {
    Write-Host "[Remote] failed to start cloudflared quick tunnel: $($_.Exception.Message)"
    exit 1
}

if (-not $p -or -not $p.Id) {
    Write-Host '[Remote] cloudflared quick tunnel did not return a PID.'
    exit 1
}

if ($PidFile) {
    $p.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline
}
exit 0
