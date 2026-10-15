<#
.SYNOPSIS
    Probe protocol handler for pan:// URIs (stands in for the production
    activation entry until the real module exists).

.DESCRIPTION
    Appends every invocation (timestamp + raw URI) to activations.jsonl, then
    parses pan://open?target=<urlencoded> and opens the target with the default
    browser. Evidence for "notification center click works after the sender
    process is gone".
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Raw
)

$ErrorActionPreference = 'Stop'
$uri = ($Raw -join ' ').Trim()
$log = Join-Path $PSScriptRoot 'activations.jsonl'
$entry = @{ utc = (Get-Date).ToUniversalTime().ToString('o'); raw = $uri } | ConvertTo-Json -Compress
Add-Content -LiteralPath $log -Value $entry -Encoding UTF8

$target = $null
if ($uri -match '^(?:pan|pan-probe)://open\?(.*)$') {
    foreach ($pair in ($Matches[1] -split '&')) {
        $idx = $pair.IndexOf('=')
        if ($idx -le 0) { continue }
        $name = $pair.Substring(0, $idx)
        $value = $pair.Substring($idx + 1)
        if ($name -eq 'target') { $target = [uri]::UnescapeDataString($value) }
    }
}
if (-not $target) {
    Add-Content -LiteralPath $log -Value (@{ utc = (Get-Date).ToUniversalTime().ToString('o'); error = 'no target' } | ConvertTo-Json -Compress) -Encoding UTF8
    exit 2
}
Start-Process -FilePath $target
Add-Content -LiteralPath $log -Value (@{ utc = (Get-Date).ToUniversalTime().ToString('o'); launched = $target } | ConvertTo-Json -Compress) -Encoding UTF8
exit 0
