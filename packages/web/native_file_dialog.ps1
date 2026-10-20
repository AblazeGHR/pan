$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    try {
        $dialog.Title = 'Select server attachments'
        $dialog.Multiselect = $true
        $dialog.CheckFileExists = $true
        $dialog.RestoreDirectory = $true
        if ($inputData.directory) {
            $initial = [IO.Path]::GetFullPath($inputData.directory)
            if (-not (Test-Path -LiteralPath $initial -PathType Container)) { throw 'Invalid initial directory' }
            $dialog.InitialDirectory = $initial
        }
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            @{ status = 'selected'; paths = @($dialog.FileNames) } | ConvertTo-Json -Compress
        } else {
            @{ status = 'cancelled'; paths = @() } | ConvertTo-Json -Compress
        }
    } finally { $dialog.Dispose() }
} catch {
    @{ status = 'failed'; paths = @() } | ConvertTo-Json -Compress
    exit 1
}
