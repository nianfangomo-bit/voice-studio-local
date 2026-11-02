param([string]$Destination = (Join-Path $PSScriptRoot 'VoiceStudio-1.0.1-download'))
$ErrorActionPreference = 'Stop'
$tag = 'v1.0.1'
$base = 'https://github.com/nianfangomo-bit/voice-studio-local/releases/download/' + $tag + '/'
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
function Receive-File([string]$Name, [string]$Hash = '') {
    if ([IO.Path]::GetFileName($Name) -ne $Name) { throw '下载文件名无效' }
    $target = Join-Path $Destination $Name
    if ((Test-Path -LiteralPath $target) -and $Hash -and ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $Hash)) { return }
    if ((Test-Path -LiteralPath $target) -and $Hash) { throw "已有文件校验不同，请移走后重试：$target" }
    $temporary = $target + '.download'
    for ($attempt=1; $attempt -le 4; $attempt++) {
        try {
            Write-Host "下载 $Name ($attempt/4)"
            Invoke-WebRequest -UseBasicParsing -Uri ($base + [Uri]::EscapeDataString($Name)) -OutFile $temporary
            if ($Hash -and ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $Hash)) { throw '校验失败' }
            Move-Item -LiteralPath $temporary -Destination $target -Force
            return
        } catch { if ($attempt -eq 4) { throw }; Start-Sleep -Seconds 3 }
    }
}
Receive-File 'SHA256SUMS.txt'
$checks = @{}
foreach ($line in (Get-Content -LiteralPath (Join-Path $Destination 'SHA256SUMS.txt'))) {
    if ($line -match '^([a-fA-F0-9]{64})\s+(.+)$') { $checks[$matches[2]]=$matches[1] }
}
foreach ($name in @('offline-package.json','VoiceStudio-Offline-Setup.exe','INSTALL.txt')) {
    if (!$checks.ContainsKey($name)) { throw "校验清单缺少 $name" }
    Receive-File $name $checks[$name]
}
$manifest = Get-Content -Raw -LiteralPath (Join-Path $Destination 'offline-package.json') | ConvertFrom-Json
if ($manifest.version -ne '1.0.1') { throw '版本不匹配' }
foreach ($part in $manifest.parts) { Receive-File $part.name $part.sha256 }
Write-Host "下载并校验完成。打开 $Destination，双击 VoiceStudio-Offline-Setup.exe。安装和后续使用不需要联网。"
