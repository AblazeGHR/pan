param(
    [string]$PidFile
)

# All startup settings now come from config.json so a single file manages
# both the local port and the cloudflared tunnel config path.
$BASE_DIR = Split-Path $PSScriptRoot -Parent
$cfgPath = Join-Path $BASE_DIR 'config.json'

$port = $null
$cfConfig = $null

if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        # Port precedence matches main.py: PAN_PORT env > config.json "port" > 8768
        if ($env:PAN_PORT) {
            $port = $env:PAN_PORT
        } elseif ($cfg.port) {
            $port = $cfg.port
        }
        # cloudflared config file is config.json -> remote.config_path
        if ($cfg.remote -and $cfg.remote.config_path) {
            $cfConfig = $cfg.remote.config_path
        }
    } catch { }
}

if (-not $port) { $port = 8768 }

# Fallback cf config path: PAN_CF_CONFIG env > user-profile default
if (-not $cfConfig) {
    if ($env:PAN_CF_CONFIG) {
        $cfConfig = $env:PAN_CF_CONFIG
    } else {
        $cfConfig = "$env:USERPROFILE\.cloudflared\config-test.yml"
    }
}

# Generate a cloudflared config with the port injected from config.json,
# so the tunnel always forwards to the port main.py actually listens on.
$content = Get-Content $cfConfig -Raw
$content = $content -replace 'http://localhost:\d+', "http://localhost:$port"
# Protocol injection: config.json remote.protocol ("auto" | "quic" | "http2")
# is appended at the yml root level. Empty/absent = no injection (cloudflared
# default auto-detect, which prefers QUIC over UDP 7844).
$proto = $null
if ($cfg -and $cfg.remote -and $cfg.remote.protocol) {
    $proto = "$($cfg.remote.protocol)".Trim()
}
if ($proto) {
    $content = $content -replace "(?m)^\s*protocol\s*:.*\r?\n?", ''
    if ($content -notmatch "`n$") { $content += "`n" }
    $content += "protocol: $proto`n"
}
$tempConfig = Join-Path $env:TEMP "pan_cf_config_$port.yml"
$content | Set-Content -Path $tempConfig -Encoding utf8

$p = Start-Process -FilePath 'cloudflared.exe' -ArgumentList 'tunnel','--config',$tempConfig,'run' -WindowStyle Minimized -PassThru
if ($PidFile) {
    $p.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline
}
