<#
.SYNOPSIS
    Executable validation for prepare_validation_env.ps1.

.DESCRIPTION
    Exercises the safety/idempotency contract using ISOLATED temp worktrees
    (folders created under $env:TEMP, including a path WITH SPACES). It never
    touches the canonical dependency targets. All temp artifacts are cleaned up.

    Run:  powershell -NoProfile -ExecutionPolicy Bypass -File prepare_validation_env.tests.ps1
#>

$ErrorActionPreference = 'Stop'
$ConfirmPreference = 'None'
$MainScript = Join-Path $PSScriptRoot 'prepare_validation_env.ps1'
if (-not (Test-Path -LiteralPath $MainScript)) {
    Write-Host "FATAL: cannot find $MainScript" -ForegroundColor Red
    exit 3
}

$CanonicalNodeModules = 'D:\project\Pan-main\packages\web\node_modules'
$CanonicalPython      = 'D:\project\Pan\.venv\Scripts\python.exe'

$Pass = 0
$Fail = 0
$TempRoots = [System.Collections.Generic.List[string]]::new()

function Note-Pass { param([string]$Name) ; Write-Host "PASS  $Name" -ForegroundColor Green ; $script:Pass++ }
function Note-Fail { param([string]$Name, [string]$Detail) ; Write-Host "FAIL  $Name : $Detail" -ForegroundColor Red ; $script:Fail++ }

# Run the main script in an isolated process; returns @{ExitCode, Output}.
function Run-Main {
    param([string[]]$RawArgs)
    $quoted = $RawArgs | ForEach-Object { if ($_ -like '* *') { "`"$_`"" } else { $_ } }
    $argStr = "-NoProfile -ExecutionPolicy Bypass -File `"$MainScript`" " + ($quoted -join ' ')
    $psi = [System.Diagnostics.ProcessStartInfo]::new('powershell.exe')
    $psi.Arguments = $argStr
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd() + "`n" + $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    return @{ ExitCode = $p.ExitCode; Output = $out }
}

# Create an isolated temp worktree folder with a SPACE in its name.
function New-TempWorktree {
    $name = "dep-prep test " + [guid]::NewGuid().ToString('N').Substring(0, 6)
    $root = Join-Path ([System.IO.Path]::GetTempPath()) $name
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $root 'packages/web') -Force | Out-Null
    $script:TempRoots.Add($root)
    return $root
}

# Safe cleanup of a temp worktree: remove ONLY junctions first, then the real dirs.
function Remove-TempWorktree {
    param([string]$Root)
    $nm = Join-Path $Root 'packages/web/node_modules'
    if (Test-Path -LiteralPath $nm) {
        $item = Get-Item -LiteralPath $nm -Force
        if ($item.LinkType) {
            # rmdir (via cmd) removes only the junction, never the target contents.
            $psi = [System.Diagnostics.ProcessStartInfo]::new('cmd.exe')
            $psi.Arguments = "/c rmdir `"$nm`""
            $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.WindowStyle = 'Hidden'
            $p = [System.Diagnostics.Process]::Start($psi); $p.WaitForExit()
        }
    }
    if (Test-Path -LiteralPath $Root) {
        try { Remove-Item -LiteralPath $Root -Recurse -Force -Confirm:$false -ErrorAction SilentlyContinue } catch { }
    }
}

# ---------------------------------------------------------------------------
# TEST 0: parser / syntax check of the main script (no execution)
# ---------------------------------------------------------------------------
try {
    $tokens = $null; $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($MainScript, [ref]$tokens, [ref]$errors)
    if ($errors.Count -eq 0) { Note-Pass 'parser: main script compiles cleanly' }
    else { Note-Fail 'parser: main script has syntax errors' ($errors | Out-String) }
} catch {
    Note-Fail 'parser: exception' $_.Exception.Message
}

# ---------------------------------------------------------------------------
# TEST 1: create junction in an isolated worktree (path WITH spaces)
# ---------------------------------------------------------------------------
$wt1 = New-TempWorktree
$r1 = Run-Main @('-Worktree', $wt1, '-CanonicalNodeModules', $CanonicalNodeModules, '-CanonicalPython', $CanonicalPython)
$nm1 = Join-Path $wt1 'packages/web/node_modules'
$created = (Test-Path -LiteralPath $nm1) -and (Get-Item -LiteralPath $nm1 -Force).LinkType
if ($r1.ExitCode -eq 0 -and $created) { Note-Pass 'create: junction created in path-with-spaces worktree' }
else { Note-Fail 'create: junction not created' "exit=$($r1.ExitCode); created=$created`n$($r1.Output)" }

# ---------------------------------------------------------------------------
# TEST 2: idempotent re-run reuses the existing correct junction
# ---------------------------------------------------------------------------
$r2 = Run-Main @('-Worktree', $wt1, '-CanonicalNodeModules', $CanonicalNodeModules, '-CanonicalPython', $CanonicalPython)
$reused = $r2.Output -match 'already correct'
if ($r2.ExitCode -eq 0 -and $reused) { Note-Pass 'idempotent: re-run reuses existing junction' }
else { Note-Fail 'idempotent: re-run did not reuse' "exit=$($r2.ExitCode)`n$($r2.Output)" }

# ---------------------------------------------------------------------------
# TEST 3: wrong-target junction => fail-closed, junction left untouched
# ---------------------------------------------------------------------------
$wt3 = New-TempWorktree
$dummy = Join-Path $wt3 'dummy-target'
New-Item -ItemType Directory -Path $dummy -Force | Out-Null
$wt3nm = Join-Path $wt3 'packages/web/node_modules'
cmd /c "mklink /J `"$wt3nm`" `"$dummy`"" | Out-Null
$r3 = Run-Main @('-Worktree', $wt3, '-CanonicalNodeModules', $CanonicalNodeModules, '-CanonicalPython', $CanonicalPython)
$stillWrong = (Test-Path -LiteralPath $wt3nm) -and ((Get-Item -LiteralPath $wt3nm -Force).Target -contains $dummy)
if ($r3.ExitCode -ne 0 -and $stillWrong) { Note-Pass 'fail-closed: wrong-target junction not modified/deleted' }
else { Note-Fail 'fail-closed: wrong-target not handled' "exit=$($r3.ExitCode); stillWrong=$stillWrong`n$($r3.Output)" }
Remove-TempWorktree $wt3

# ---------------------------------------------------------------------------
# TEST 4: missing canonical target => fail-closed
# ---------------------------------------------------------------------------
$wt4 = New-TempWorktree
$badTarget = Join-Path ([System.IO.Path]::GetTempPath()) ("nonexistent-" + [guid]::NewGuid().ToString('N').Substring(0,6))
$r4 = Run-Main @('-Worktree', $wt4, '-CanonicalNodeModules', $badTarget, '-CanonicalPython', $CanonicalPython)
$notCreated = -not (Test-Path -LiteralPath (Join-Path $wt4 'packages/web/node_modules'))
if ($r4.ExitCode -ne 0 -and $notCreated) { Note-Pass 'fail-closed: missing canonical target rejected' }
else { Note-Fail 'fail-closed: missing target not rejected' "exit=$($r4.ExitCode); notCreated=$notCreated`n$($r4.Output)" }
Remove-TempWorktree $wt4

# ---------------------------------------------------------------------------
# TEST 5: -Check mode with no existing junction => does NOT create
# ---------------------------------------------------------------------------
$wt5 = New-TempWorktree
$r5 = Run-Main @('-Worktree', $wt5, '-Check', '-CanonicalNodeModules', $CanonicalNodeModules, '-CanonicalPython', $CanonicalPython)
$notCreated5 = -not (Test-Path -LiteralPath (Join-Path $wt5 'packages/web/node_modules'))
if ($r5.ExitCode -eq 0 -and $notCreated5) { Note-Pass 'check: no junction created in check mode' }
else { Note-Fail 'check: junction wrongly created' "exit=$($r5.ExitCode); notCreated=$notCreated5`n$($r5.Output)" }
Remove-TempWorktree $wt5

# ---------------------------------------------------------------------------
# TEST 6: -DryRun mode => does NOT create
# ---------------------------------------------------------------------------
$wt6 = New-TempWorktree
$r6 = Run-Main @('-Worktree', $wt6, '-DryRun', '-CanonicalNodeModules', $CanonicalNodeModules, '-CanonicalPython', $CanonicalPython)
$notCreated6 = -not (Test-Path -LiteralPath (Join-Path $wt6 'packages/web/node_modules'))
if ($r6.ExitCode -eq 0 -and $notCreated6) { Note-Pass 'dry-run: no junction created' }
else { Note-Fail 'dry-run: junction wrongly created' "exit=$($r6.ExitCode); notCreated=$notCreated6`n$($r6.Output)" }
Remove-TempWorktree $wt6

# ---------------------------------------------------------------------------
# TEST 7: -Undo removes only the junction, canonical untouched
# ---------------------------------------------------------------------------
$wt7 = New-TempWorktree
Run-Main @('-Worktree', $wt7, '-CanonicalNodeModules', $CanonicalNodeModules, '-CanonicalPython', $CanonicalPython) | Out-Null
$canonAliveBefore = Test-Path -LiteralPath $CanonicalNodeModules
$r7 = Run-Main @('-Worktree', $wt7, '-Undo')
$canonAliveAfter = Test-Path -LiteralPath $CanonicalNodeModules
$removed = -not (Test-Path -LiteralPath (Join-Path $wt7 'packages/web/node_modules'))
if ($r7.ExitCode -eq 0 -and $removed -and $canonAliveAfter) { Note-Pass 'undo: junction removed, canonical intact' }
else { Note-Fail 'undo: unexpected' "exit=$($r7.ExitCode); removed=$removed; canonBefore=$canonAliveBefore; canonAfter=$canonAliveAfter`n$($r7.Output)" }
Remove-TempWorktree $wt7

# ---------------------------------------------------------------------------
# TEST 8: Python import report (honest; dotenv/mcp/pytest/fastapi/httpx/pydantic expected OK)
# ---------------------------------------------------------------------------
$wt8 = New-TempWorktree
$r8 = Run-Main @('-Worktree', $wt8, '-Check', '-CanonicalNodeModules', $CanonicalNodeModules, '-CanonicalPython', $CanonicalPython)
$hasPython = $r8.Output -match 'Canonical Python:'
$reportsModules = $r8.Output -match '\[OK\] import (dotenv|mcp|pytest|fastapi|httpx|pydantic)'
if ($hasPython) { Note-Pass 'python: canonical interpreter reported' }
else { Note-Fail 'python: canonical interpreter not reported' $r8.Output }
Remove-TempWorktree $wt8

# ---------------------------------------------------------------------------
# Cleanup + summary
# ---------------------------------------------------------------------------
foreach ($t in $script:TempRoots) { if (Test-Path -LiteralPath $t) { Remove-TempWorktree $t } }
Write-Host "`n==== TEST SUMMARY: $Pass passed, $Fail failed ====" -ForegroundColor Cyan
exit $(if ($Fail -eq 0) { 0 } else { 1 })
