<#
.SYNOPSIS
    Prepare a Pan worktree for localhost validation by reusing SHARED canonical
    dependencies instead of downloading a copy per worktree.

.DESCRIPTION
    Goal: "install once, validate everywhere". This script makes
    <Worktree>/packages/web/node_modules a junction (NOT a copy) pointing at the
    canonical frontend node_modules, and verifies the canonical Python environment
    by importing the expected modules. It is intentionally FAIL-CLOSED and idempotent.

    Safety model:
      * Never creates a .venv inside any worktree.
      * Never modifies the canonical dependency targets.
      * Never recursively deletes anything. The only deletion it ever performs is
        removing a single junction it created (via -Undo), and only when that path
        is provably a junction/symlink (never a real directory).
      * Python package repair (if ever needed) is opt-in via -FixPython and acts
        ONLY on the canonical venv, recording before/after evidence. Default is
        check-only; it will NOT install anything silently.
      * -Check is fully read-only, even when an install switch is also supplied.
      * Native commands are launched via System.Diagnostics.Process so a missing
        package (e.g. playwright) is reported honestly instead of aborting the run.

.PARAMETER Worktree
    Root path of the worktree to prepare. Required for Prepare/Undo modes.

.PARAMETER CanonicalNodeModules
    Canonical frontend node_modules (the single shared install).
    Default: D:\project\Pan-main\packages\web\node_modules

.PARAMETER CanonicalPython
    Canonical Python interpreter (the single shared Pan venv).
    Default: D:\project\Pan-main\.venv\Scripts\python.exe

.PARAMETER PythonModules
    Python modules whose import is verified against the canonical interpreter.
    Default: dotenv, mcp, pytest, fastapi, httpx, pydantic

.PARAMETER DryRun
    Simulate all actions and print what WOULD happen. No filesystem changes.

.PARAMETER Check
    Verify/report only. Never creates the junction, never fixes Python.
    (Equivalent to a read-only audit.)

.PARAMETER FixPython
    Opt-in. Allows Python package repair, but ONLY on the canonical venv.
    Requires -FixPackages to name what to install. Records before/after pip freeze.
    Note: dotenv/exceptiongroup were already repaired by MA; do NOT reinstall them
    pointlessly. Default (without -FixPython) is check-only.

.PARAMETER FixPackages
    Space-separated package specs to install into the canonical venv when -FixPython
    is also set. Example: -FixPython -FixPackages "requests" "httpx==0.27.0"

.PARAMETER PlaywrightCache
    Path to a SHARED Playwright browser cache. When set, the script reports the
    recommended PLAYWRIGHT_BROWSERS_PATH env var so multiple worktrees reuse one
    browser download.

.PARAMETER InstallPlaywright
    Opt-in. Attempts `playwright install`. THIS TRIGGERS A NETWORK DOWNLOAD and
    grows the local browser cache. It does NOT claim that browser E2E passes.

.PARAMETER Undo
    Remove ONLY the junction created inside the worktree
    (<Worktree>/packages/web/node_modules). Never deletes the canonical target or
    any real directory. Refuses to delete a real directory.

.PARAMETER LogPath
    Optional path to append a human-readable report.

.EXAMPLE
    # Idempotent prepare of a worktree (safe, only creates a junction):
    .\prepare_validation_env.ps1 -Worktree "D:\project\Pan\data\workdirs\my-ta"

    # Just audit, no changes:
    .\prepare_validation_env.ps1 -Worktree "..." -Check

    # Only remove the junction this script created:
    .\prepare_validation_env.ps1 -Worktree "..." -Undo
#>

[CmdletBinding(DefaultParameterSetName = 'Prepare', SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Prepare')]
    [Parameter(Mandatory = $true, ParameterSetName = 'Undo')]
    [string]$Worktree,

    [string]$CanonicalNodeModules = 'D:\project\Pan-main\packages\web\node_modules',
    [string]$CanonicalPython      = 'D:\project\Pan-main\.venv\Scripts\python.exe',
    [string[]]$PythonModules      = @('dotenv', 'mcp', 'pytest', 'fastapi', 'httpx', 'pydantic'),

    [switch]$DryRun,
    [switch]$Check,

    [switch]$FixPython,
    [string[]]$FixPackages = @(),

    [string]$PlaywrightCache,
    [switch]$InstallPlaywright,

    [Parameter(ParameterSetName = 'Undo')]
    [switch]$Undo,

    [string]$LogPath
)

# ---------------------------------------------------------------------------
# Globals / helpers
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
$ConfirmPreference = 'None'
$script:ExitCode = 0
$script:Report = [System.Collections.Generic.List[string]]::new()
$script:AnyMissing = $false

function Add-Report {
    param([string]$Line, [string]$Level = 'INFO')
    $stamp = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    $entry = "[$stamp][$Level] $Line"
    $script:Report.Add($entry)
    switch ($Level) {
        'ERROR' { Write-Host $entry -ForegroundColor Red }
        'WARN'  { Write-Host $entry -ForegroundColor Yellow }
        'OK'    { Write-Host $entry -ForegroundColor Green }
        default { Write-Host $entry }
    }
    if ($LogPath) {
        try { Add-Content -LiteralPath $LogPath -Value $entry -Encoding utf8 } catch { }
    }
}

function Fail-Closed {
    param([string]$Reason)
    Add-Report $Reason 'ERROR'
    $script:ExitCode = 2
    Write-Summary
    exit 2
}

function Write-Summary {
    if ($script:AnyMissing) {
        Add-Report 'One or more canonical Python modules are MISSING (reported above).' 'WARN'
    }
    Add-Report "Exit code: $script:ExitCode" 'INFO'
}

# Runs a native command via Process (no PowerShell native-error wrapping).
# Returns @{ ExitCode; Output } where Output = stdout+stderr merged.
function Invoke-Native {
    param([string]$CommandLine)
    $psi = [System.Diagnostics.ProcessStartInfo]::new('cmd.exe')
    $psi.Arguments = "/c $CommandLine"
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $p = [System.Diagnostics.Process]::Start($psi)
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    return @{ ExitCode = $p.ExitCode; Output = ($stdout + $stderr).Trim() }
}

# Returns an object describing a path: is it a link, its target(s), is it a dir.
function Get-LinkInfo {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -ErrorAction SilentlyContinue)) {
        return @{ Exists = $false; IsLink = $false; LinkType = $null; Target = $null; IsDir = $false }
    }
    $item = Get-Item -LiteralPath $Path -Force
    $isLink = [bool]$item.LinkType
    return @{
        Exists = $true
        IsLink = $isLink
        IsJunction = ($item.LinkType -eq 'Junction')
        LinkType = $item.LinkType
        # Coerce to a single string. PowerShell unrolls single-element arrays, so an
        # explicit [string] cast keeps the full target path (not its first character).
        Target = if ($isLink) { [string]($item.Target) } else { $null }
        IsDir  = $item.PSIsContainer
    }
}

function Resolve-Normalized {
    param([string]$Path)
    # Strip NT-namespace / long-path prefixes that reparse points may carry
    # (e.g. "\??\D:\..." or "\\?\D:\...").
    $p = $Path -replace '^\\\\\?\\', '' -replace '^\\\?\?\\', ''
    try {
        $r = Resolve-Path -LiteralPath $p -ErrorAction Stop
        return $r.Path.TrimEnd('\').ToLowerInvariant()
    } catch {
        return $p.TrimEnd('\').ToLowerInvariant()
    }
}

# Runs an executable DIRECTLY (no cmd /c) so quotes in arguments are handled by
# the OS loader rather than cmd's legacy quote stripping.
# Returns @{ ExitCode; Output } where Output = stdout+stderr merged.
function Invoke-Exe {
    param([string]$FilePath, [string]$Arguments)
    $psi = [System.Diagnostics.ProcessStartInfo]::new($FilePath)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $psi.Arguments = $Arguments
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    return @{ ExitCode = $p.ExitCode; Output = ($out + $err).Trim() }
}

function Assert-CanonicalTarget {
    param([string]$Target, [string]$Label)
    if (-not (Test-Path -LiteralPath $Target -ErrorAction SilentlyContinue)) {
        Fail-Closed "$Label target does NOT exist (fail-closed): $Target"
    }
    $item = Get-Item -LiteralPath $Target -Force
    if (-not $item.PSIsContainer) {
        Fail-Closed "$Label target is NOT a directory (fail-closed): $Target"
    }
    Add-Report "$Label target verified (directory): $Target" 'OK'
}

function Assert-WorktreeLayout {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container -ErrorAction SilentlyContinue)) {
        Fail-Closed "Worktree root does NOT exist or is not a directory (fail-closed): $Root"
    }
    $web = Join-Path $Root 'packages/web'
    if (-not (Test-Path -LiteralPath $web -PathType Container -ErrorAction SilentlyContinue)) {
        Fail-Closed "Worktree packages/web directory does NOT exist (fail-closed): $web"
    }
}

# ---------------------------------------------------------------------------
# Python verification
# ---------------------------------------------------------------------------
function Test-CanonicalPython {
    param([string]$Python, [string[]]$Modules)

    if (-not (Test-Path -LiteralPath $Python -ErrorAction SilentlyContinue)) {
        Add-Report "Canonical Python interpreter NOT FOUND: $Python" 'ERROR'
        $script:AnyMissing = $true
        $script:ExitCode = 2
        return
    }
    Add-Report "Canonical Python: $Python" 'OK'
    $ver = Invoke-Exe $Python '--version'
    Add-Report "  $($ver.Output)"

    foreach ($m in $Modules) {
        $r = Invoke-Exe $Python "-c `"import $m`""
        if ($r.ExitCode -eq 0) {
            Add-Report "  [OK] import $m" 'OK'
        } else {
            Add-Report "  [MISSING] import $m  (stderr: $($r.Output))" 'WARN'
            $script:AnyMissing = $true
        }
    }
}

function Repair-CanonicalPython {
    param([string]$Python, [string[]]$Packages)

    if (-not $FixPython -or $Check) {
        Add-Report 'Python repair skipped (default/check mode is read-only).' 'INFO'
        return
    }
    if ($Packages.Count -eq 0) {
        Add-Report '-FixPython set but no -FixPackages given. Nothing to install.' 'WARN'
        return
    }
    # IMPORTANT: only ever act on the canonical venv.
    Add-Report "Python repair is OPT-IN and limited to canonical venv: $Python" 'WARN'
    Add-Report "Packages requested: $($Packages -join ', ')" 'WARN'
    Add-Report 'NOTE: dotenv/exceptiongroup were already repaired by MA; do not reinstall them.' 'INFO'

    $before = Invoke-Exe $Python "-m pip freeze"
    Add-Report '--- pip freeze BEFORE ---' 'INFO'
    $before.Output -split "`n" | ForEach-Object { if ($_.Trim()) { Add-Report "  $_" } }

    if ($DryRun) {
        Add-Report "[dry-run] would run: $Python -m pip install $($Packages -join ' ')" 'INFO'
        return
    }

    $specs = ($Packages | ForEach-Object { "`"$_`"" }) -join ' '
    $r = Invoke-Exe $Python "-m pip install $specs"
    $r.Output -split "`n" | ForEach-Object { if ($_.Trim()) { Add-Report "  $_" } }
    if ($r.ExitCode -ne 0) {
        Fail-Closed "pip install failed for canonical venv; BEFORE evidence preserved above. Manual review required."
    }

    $after = Invoke-Exe $Python "-m pip freeze"
    Add-Report '--- pip freeze AFTER ---' 'INFO'
    $after.Output -split "`n" | ForEach-Object { if ($_.Trim()) { Add-Report "  $_" } }
}

# ---------------------------------------------------------------------------
# Playwright (honest reporting, opt-in install, never claims E2E success)
# ---------------------------------------------------------------------------
function Test-Playwright {
    param([string]$Python, [string]$Cache, [switch]$Install)

    if ($Cache) {
        Add-Report "Shared Playwright browser cache (recommended): $Cache" 'INFO'
        Add-Report "  Reuse by exporting PLAYWRIGHT_BROWSERS_PATH='$Cache' before any browser test." 'INFO'
    }

    if (-not (Test-Path -LiteralPath $Python -PathType Leaf -ErrorAction SilentlyContinue)) {
        Add-Report 'Skipping Playwright import check because canonical Python is unavailable.' 'WARN'
        return
    }
    $r = Invoke-Exe $Python "-c `"import playwright`""
    $hasPkg = ($r.ExitCode -eq 0)

    if (-not $hasPkg) {
        Add-Report 'Playwright Python package NOT installed in canonical venv; Python browser harnesses cannot run.' 'WARN'
        Add-Report 'Node Playwright and shared browser binaries are not checked by this Python import probe.' 'INFO'
    } else {
        Add-Report "Playwright package present in canonical venv." 'OK'
        # The package import alone does not establish a usable browser binary.
        Add-Report 'Browser binaries and launch are not checked by this script; run a browser smoke check before claiming E2E capability.' 'INFO'
    }

    if ($Install) {
        if ($Check) {
            Add-Report 'InstallPlaywright ignored because -Check is read-only; no network or filesystem write.' 'WARN'
            return
        }
        Add-Report 'InstallPlaywright requested: this will DOWNLOAD browser binaries over the NETWORK' 'WARN'
        Add-Report 'and grow the local browser cache. It does NOT guarantee browser E2E passes.' 'WARN'
        if ($DryRun) {
            Add-Report "[dry-run] would run: $Python -m playwright install" 'INFO'
            return
        }
        if ($Cache) { $env:PLAYWRIGHT_BROWSERS_PATH = $Cache }
        $ri = Invoke-Exe $Python "-m playwright install"
        $ri.Output -split "`n" | ForEach-Object { if ($_.Trim()) { Add-Report "  $_" } }
        Add-Report 'Playwright install attempted. Re-run Test-Playwright to confirm binaries; E2E still unverified until a real browser test runs.' 'WARN'
    }
}

# ---------------------------------------------------------------------------
# Junction prepare / undo
# ---------------------------------------------------------------------------
function Ensure-NodeModulesJunction {
    param([string]$Worktree, [string]$Target)

    $linkPath = Join-Path $Worktree 'packages/web/node_modules'

    Assert-CanonicalTarget -Target $Target -Label 'Canonical node_modules'

    $info = Get-LinkInfo -Path $linkPath

    if ($info.Exists) {
        if ($info.IsLink) {
            if (-not $info.IsJunction) {
                Fail-Closed ("Existing link at '$linkPath' is '$($info.LinkType)', not a junction. " +
                             'Refusing to modify it (fail-closed).')
            }
            $want = Resolve-Normalized $Target
            $have = Resolve-Normalized $info.Target
            if ($want -eq $have) {
                Add-Report "Junction already correct; reusing (idempotent): $linkPath -> $($info.Target)" 'OK'
                return
            }
            # Wrong target: fail closed, never delete.
            Fail-Closed ("Existing link at '$linkPath' points to WRONG target '$($info.Target)' " +
                         "(expected '$Target'). Refusing to modify. Remove it manually after review.")
        }
        else {
            # Real directory present: fail closed, never delete user data.
            Fail-Closed ("Existing REAL directory at '$linkPath'. Refusing to delete a real directory. " +
                         "Move/remove it manually if you intend to use the shared junction.")
        }
    }

    # Nothing exists -> create junction.
    if ($Check -or $DryRun) {
        Add-Report "[dry-run/check] would create junction: $linkPath -> $Target" 'INFO'
        return
    }

    $parent = Split-Path -Parent $linkPath
    if (-not (Test-Path -LiteralPath $parent -ErrorAction SilentlyContinue)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Add-Report "Created parent dir: $parent" 'INFO'
    }

    # mklink /J creates a directory junction (matches the existing Pan repo pattern).
    Add-Report "Creating junction: cmd /c mklink /J `"$linkPath`" `"$Target`"" 'INFO'
    $r = Invoke-Native "mklink /J `"$linkPath`" `"$Target`""
    Add-Report "  $($r.Output)"
    if ($r.ExitCode -ne 0) {
        Fail-Closed "Failed to create junction (mklink exit $($r.ExitCode)). Canonical target untouched."
    }
        # Verify
        $verify = Get-LinkInfo -Path $linkPath
    if ($verify.IsJunction -and ((Resolve-Normalized $verify.Target) -eq (Resolve-Normalized $Target))) {
            Add-Report "Junction created and verified: $linkPath -> $($verify.Target)" 'OK'
            $marker = Join-Path $parent '.pan-validation-node-modules.junction'
            Set-Content -LiteralPath $marker -Value $Target -Encoding utf8 -NoNewline
            Add-Report "Recorded script ownership marker: $marker" 'INFO'
    } else {
        Fail-Closed "Junction creation did not verify correctly. State unknown; inspect manually."
    }
}

function Undo-NodeModulesJunction {
    param([string]$Worktree)

    $linkPath = Join-Path $Worktree 'packages/web/node_modules'
    $info = Get-LinkInfo -Path $linkPath

    if (-not $info.Exists) {
        Add-Report "Nothing to undo (no path at '$linkPath')." 'INFO'
        return
    }
    if (-not $info.IsLink) {
        Fail-Closed ("Path at '$linkPath' is a REAL directory, not a junction. " +
                     "Refusing to delete. Manual intervention required.")
    }
    if (-not $info.IsJunction) {
        Fail-Closed ("Path at '$linkPath' is '$($info.LinkType)', not a junction. Refusing to delete.")
    }
    $marker = Join-Path (Split-Path -Parent $linkPath) '.pan-validation-node-modules.junction'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf -ErrorAction SilentlyContinue)) {
        Fail-Closed "Junction at '$linkPath' has no script ownership marker. Refusing to delete it."
    }
    $markerValue = (Get-Content -LiteralPath $marker -Raw).Trim()
    if ((Resolve-Normalized $info.Target) -ne (Resolve-Normalized $markerValue)) {
        Fail-Closed "Junction ownership marker does not match '$linkPath'. Refusing to delete it."
    }
    if ($DryRun) {
        Add-Report "[dry-run] would remove junction: $linkPath (target: $($info.Target))" 'INFO'
        return
    }
    # NOTE: PowerShell's native Remove-Item throws a NullReferenceException on some
    # versions when called on a reparse point (junction). Use `cmd /c rmdir`, which
    # removes only the junction (reparse point), never the target's contents.
    $r = Invoke-Native "rmdir `"$linkPath`""
    if ($r.ExitCode -ne 0) {
        Fail-Closed "Failed to remove junction (rmdir exit $($r.ExitCode)): $linkPath. Output: $($r.Output)"
    }
    if (Test-Path -LiteralPath $linkPath) {
        Fail-Closed "Failed to remove junction: $linkPath still exists."
    }
    Remove-Item -LiteralPath $marker -Force -ErrorAction Stop
    Add-Report "Removed junction: $linkPath (canonical target untouched)" 'OK'
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Add-Report "=== prepare_validation_env.ps1 ===" 'INFO'
Add-Report "Mode: $(if ($Undo) { 'Undo' } elseif ($Check) { 'Check' } elseif ($DryRun) { 'DryRun' } else { 'Prepare' })" 'INFO'
Add-Report "Worktree: $Worktree" 'INFO'
Assert-WorktreeLayout -Root $Worktree

if ($Undo) {
    Undo-NodeModulesJunction -Worktree $Worktree
    Write-Summary
    exit $script:ExitCode
}

# Verify canonical Python exists; report modules. Then ensure junction + playwright report.
if (-not (Test-Path -LiteralPath $CanonicalPython -ErrorAction SilentlyContinue)) {
    Add-Report "Canonical Python NOT FOUND: $CanonicalPython" 'ERROR'
    $script:AnyMissing = $true
    $script:ExitCode = 2
} else {
    Test-CanonicalPython -Python $CanonicalPython -Modules $PythonModules
    Repair-CanonicalPython -Python $CanonicalPython -Packages $FixPackages
}

Test-Playwright -Python $CanonicalPython -Cache $PlaywrightCache -Install:$InstallPlaywright

Ensure-NodeModulesJunction -Worktree $Worktree -Target $CanonicalNodeModules

Write-Summary
exit $script:ExitCode
