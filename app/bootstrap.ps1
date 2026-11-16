# Bootstrap only: the Python launcher reads the authoritative database settings.
# Process-scoped execution policy does not change machine/user policy.
param([ValidateSet('launch', 'migrate')][string]$Mode = 'launch')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    $runtimePath = Join-Path $PSScriptRoot 'engine\runtime\python.exe'
    $bootstrapPath = Join-Path $PSScriptRoot 'data\bootstrap.json'
    if (Test-Path -LiteralPath $bootstrapPath -PathType Leaf) {
        $saved = Get-Content -LiteralPath $bootstrapPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($saved.runtime -and (Test-Path -LiteralPath $saved.runtime -PathType Leaf)) {
            $runtimePath = [string]$saved.runtime
        }
    }
    if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        $picker.Title = 'Select trusted GPT-SoVITS runtime/python.exe'
        $picker.Filter = 'Python runtime (python.exe)|python.exe'
        if ($picker.ShowDialog() -ne 'OK') { exit 0 }
        $runtimePath = $picker.FileName
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "Run this trusted local environment?`n$runtimePath`nNo packages will be installed.",
            'Voice Studio', 'YesNo', 'Warning')
        if ($answer -ne 'Yes') { exit 0 }
        if ($Mode -eq 'launch') {
            $null = New-Item -ItemType Directory -Force -Path (Split-Path $bootstrapPath)
            $tempPath = "$bootstrapPath.$([guid]::NewGuid().ToString('N')).tmp"
            $json = @{runtime=$runtimePath} | ConvertTo-Json
            [IO.File]::WriteAllText($tempPath, $json, (New-Object Text.UTF8Encoding($false)))
            Move-Item -LiteralPath $tempPath -Destination $bootstrapPath -Force
        }
    }
    if ($Mode -eq 'migrate') {
        & $runtimePath -I -X utf8 -B (Join-Path $PSScriptRoot 'migrate_data.py') --wizard
    } else {
        & $runtimePath -I -X utf8 -B (Join-Path $PSScriptRoot 'launcher.py')
    }
    exit $LASTEXITCODE
} catch {
    [System.Windows.Forms.MessageBox]::Show(
        "Startup failed. Existing data is preserved.`n$($_.Exception.Message)",
        'Voice Studio', 'OK', 'Error') | Out-Null
    exit 1
}
