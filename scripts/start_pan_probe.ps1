param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("ExistingMainPid", "Port", "RemoteState", "QuickState", "QuickUrl", "Ready", "ProcessAlive")]
    [string]$Action,
    [string]$BaseDir,
    [int]$Port,
    [string]$LogFile
)

$ErrorActionPreference = "Stop"

if (-not $BaseDir) {
    throw "Pan checkout path is required"
}

switch ($Action) {
    "ProcessAlive" {
        if (Get-Process -Id $env:PAN_MAIN_PID -ErrorAction SilentlyContinue) {
            exit 0
        }
        exit 1
    }
    "Ready" {
        $url = "http://127.0.0.1:$Port/api/sessions?summary=1"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2 | Out-Null
            exit 0
        } catch {
            exit 1
        }
    }
    "ExistingMainPid" {
        $base = $BaseDir.Replace('\', '/').TrimEnd('/')
        $root = "$base/"
        $process = Get-CimInstance Win32_Process | Where-Object {
            $_.Name -match '^(python|pythonw|uvicorn)(\.exe)?$' -and
            $_.CommandLine -and
            $_.CommandLine.Replace('\', '/').Contains($root) -and
            (($_.CommandLine -match 'main\.py') -or
             ($_.CommandLine -match 'packages[\\/]web[\\/]server') -or
             ($_.CommandLine -match 'uvicorn'))
        } | Select-Object -First 1
        if ($process) {
            $process.ProcessId
        }
    }
    "Port" {
        $configPath = Join-Path $BaseDir "config.json"
        if (Test-Path -LiteralPath $configPath) {
            $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
            if ($config.port) { $config.port } else { 8768 }
        } else {
            8768
        }
    }
    "RemoteState" {
        $configPath = Join-Path $BaseDir "config.json"
        try {
            if (-not (Test-Path -LiteralPath $configPath)) {
                "missing"
            } else {
                $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
                if ($config.remote -and
                    ($config.remote.PSObject.Properties.Name -contains "enabled") -and
                    ($config.remote.enabled -is [bool]) -and $config.remote.enabled) {
                    "enabled"
                } else {
                    "disabled"
                }
            }
        } catch {
            "invalid"
        }
    }
    "QuickState" {
        $configPath = Join-Path $BaseDir "config.json"
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        if ($config.remote -and
            ($config.remote.PSObject.Properties.Name -contains "quick_tunnel")) {
            if ($config.remote.quick_tunnel) { "quick" } else { "named" }
        } else {
            "quick"
        }
    }
    "QuickUrl" {
        if (-not $LogFile) { throw "Quick tunnel log path is required" }
        $url = $null
        for ($index = 0; $index -lt 30; $index++) {
            if (Test-Path -LiteralPath $LogFile) {
                $match = Select-String -LiteralPath $LogFile -Pattern 'https://[a-zA-Z0-9\-]+\.trycloudflare\.com' |
                    Select-Object -First 1
                if ($match) {
                    $url = $match.Matches[0].Value
                    break
                }
            }
            Start-Sleep -Milliseconds 500
        }
        if ($url) {
            Write-Host ("[OK] Quick tunnel URL: " + $url)
        } else {
            Write-Host "[INFO] trycloudflare.com URL not captured yet; watch the log file above."
        }
    }
}
