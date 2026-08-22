param(
    [string]$Python,
    [string]$MainPy,
    [string]$WorkDir,
    [string]$PidFile
)

# Start main.py directly with the venv interpreter — no cmd /k wrapper, so the
# recorded PID is the python process itself and stop_pan.bat's process-tree
# kill (/T) takes down main.py plus any child processes (incl. the QQ bot).
$p = Start-Process -FilePath $Python -ArgumentList "`"$MainPy`"" -WorkingDirectory $WorkDir -WindowStyle Normal -PassThru
$p.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline
