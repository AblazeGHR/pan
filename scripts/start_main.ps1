param(
    [string]$Activate,
    [string]$MainPy,
    [string]$WorkDir,
    [string]$PidFile
)

$p = Start-Process -FilePath 'cmd' -ArgumentList "/k call `"$Activate`" && python `"$MainPy`"" -WorkingDirectory $WorkDir -WindowStyle Normal -PassThru
$p.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline
