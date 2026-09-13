# Shared Windows bootstrap resolver for Pan-owned Python processes.
# The Python-side resolver lives in packages/core/config.py; this bootstrap
# copy is required before any Python interpreter has been selected. Keep the
# priority and validation rules aligned with it.

function New-PanPythonCandidate([string]$Source, [string]$Command, [object[]]$ExtraArgs) {
    if (-not $Command -or -not $Command.Trim()) { return $null }
    [pscustomobject]@{
        Source = $Source
        Argv = @($Command.Trim()) + @($ExtraArgs | Where-Object { $_ -is [string] -and $_ })
    }
}

function Get-PanPythonConfigCandidate([object]$Value) {
    if ($null -eq $Value) { return $null }
    if ($Value -is [string]) {
        return New-PanPythonCandidate "config.json python" $Value @()
    }
    if ($Value -is [System.Array]) {
        if ($Value.Count -lt 1 -or $Value[0] -isnot [string] -or
            @($Value | Where-Object { $_ -isnot [string] -or -not $_ }).Count -gt 0) { return $null }
        return New-PanPythonCandidate "config.json python" $Value[0] @($Value | Select-Object -Skip 1)
    }
    $command = $Value.command
    $extra = if ($null -eq $Value.args) { @() } else { @($Value.args) }
    if ($null -ne $Value.args -and
        @($Value.args | Where-Object { $_ -isnot [string] -or -not $_ }).Count -gt 0) { return $null }
    return New-PanPythonCandidate "config.json python" $command $extra
}

function Test-PanPythonCandidate([object]$Candidate) {
    if ($null -eq $Candidate -or $Candidate.Argv.Count -lt 1) { return $false }
    $command = [string]$Candidate.Argv[0]
    $looksLikePath = $command.Contains('\') -or $command.Contains('/') -or
        ($command.Length -gt 1 -and $command[1] -eq ':') -or
        $command.ToLowerInvariant().EndsWith('.exe') -or
        $command.ToLowerInvariant().EndsWith('.cmd') -or
        $command.ToLowerInvariant().EndsWith('.bat')
    if ($looksLikePath) {
        if (-not (Test-Path -LiteralPath $command -PathType Leaf)) { return $false }
        $suffix = [IO.Path]::GetExtension($command).ToLowerInvariant()
        if ($suffix -and @('.exe', '.com', '.cmd', '.bat') -notcontains $suffix) { return $false }
    } elseif (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        return $false
    }
    $extra = @($Candidate.Argv | Select-Object -Skip 1)
    & $command @extra -c "import fastapi, uvicorn, websockets, psutil, httpx; from mcp.server.fastmcp import FastMCP" *> $null
    return $LASTEXITCODE -eq 0
}

function Resolve-PanPython([string]$Root, [string]$Explicit) {
    if ($Explicit) {
        $candidates = @(New-PanPythonCandidate "explicit argument" $Explicit @())
    } else {
        $configCandidate = $null
        $configPath = Join-Path $Root "config.json"
        if (Test-Path -LiteralPath $configPath -PathType Leaf) {
            try {
                $configObject = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
                $configCandidate = Get-PanPythonConfigCandidate $configObject.python
            } catch {
                Write-Host "[WARN] config.json python could not be read; trying fallback Pan Python"
            }
        }
        $candidates = @()
        if ($configCandidate) { $candidates += $configCandidate }
        if ($env:PAN_PYTHON) {
            $candidates += New-PanPythonCandidate "PAN_PYTHON" $env:PAN_PYTHON @()
        }
        $candidates += New-PanPythonCandidate "checkout .venv" (Join-Path $Root ".venv\Scripts\python.exe") @()
        $pathPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pathPython) { $candidates += New-PanPythonCandidate "python.exe on PATH" $pathPython.Source @() }
    }
    foreach ($candidate in $candidates) {
        if (Test-PanPythonCandidate $candidate) { return $candidate }
        Write-Host "[WARN] Ignoring unusable $($candidate.Source); trying next Pan Python"
    }
    throw "No usable Pan Python interpreter was found (config.json python > PAN_PYTHON > checkout .venv > PATH)"
}
