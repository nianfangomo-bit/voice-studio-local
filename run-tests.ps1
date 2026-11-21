# Voice Studio 仓库根目录统一测试入口。
# 用法（在仓库根目录）：
#   pwsh -File .\run-tests.ps1
#   pwsh -File .\run-tests.ps1 -Python "F:\VoiceStudio-install-20261117-rc1032\engine\runtime\python.exe"
# 退出码：0 表示 Python 全量测试和前端检查都通过；非 0 表示失败，并保留实际退出码。
param(
    [string]$Python = '',
    [string]$Node = 'node',
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location $root

if (-not $Python) {
    if ($env:VOICE_STUDIO_PYTHON) { $Python = $env:VOICE_STUDIO_PYTHON } else { $Python = 'python' }
}
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'

$exitCode = 0
Write-Host '== Python 测试（完整套件）=='
& $Python -B -m unittest discover -s app/tests -t app/tests -v
if ($LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE }

if ($exitCode -eq 0 -and -not $SkipFrontend) {
    Write-Host '== 前端检查（无需安装依赖）=='
    & $Node app/tests/voice-selection.test.mjs
    if ($LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE }
}

if ($exitCode -eq 0) {
    Write-Host '全部测试通过'
} else {
    Write-Host ('测试失败，退出码 ' + $exitCode)
}
exit $exitCode