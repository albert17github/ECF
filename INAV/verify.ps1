param(
    [Parameter(Mandatory=$true)][string]$Port,
    [Parameter(Mandatory=$true)][string]$Uid,
    [Parameter(Mandatory=$true)][string]$StlinkSerial,
    [Parameter(Mandatory=$true)][switch]$UnloadedBench,
    [string]$Python='python',
    [string]$Cube=$env:ECF_CUBE_PROGRAMMER,
    [string]$WslDistribution,
    [switch]$FlashFactoryImage
)
$ErrorActionPreference='Stop'
if (!$UnloadedBench -or $Uid -notmatch '^[0-9a-fA-F]{24}$') { throw 'Specify the unloaded bench and 12-byte MSP UID' }
if (!$Cube) { $Cube='C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe' }
$Cube=(Resolve-Path -LiteralPath $Cube).Path
$ecfRoot=(Resolve-Path -LiteralPath $PSScriptRoot).Path
$ecfRelease=Get-Content -LiteralPath (Join-Path $ecfRoot 'release.json') -Raw | ConvertFrom-Json
if ($ecfRelease.firmware_build_pending -or $ecfRelease.firmware.source_manifest_sha256 -ne $ecfRelease.source_manifest_sha256) { throw 'Rebuild the matching source revision before verification' }
$ecfImage=Join-Path $ecfRoot $ecfRelease.firmware.image
$ecfElf=Join-Path $ecfRoot $ecfRelease.firmware.elf
if ((Get-FileHash -LiteralPath $ecfImage -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ecfRelease.firmware.sha256 -or (Get-FileHash -LiteralPath $ecfElf -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ecfRelease.firmware.elf_sha256) { throw 'Frozen firmware digest mismatch' }
$ecfStamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$ecfOut=Join-Path $ecfRoot ".build/verification/$ecfStamp"
if (Test-Path -LiteralPath $ecfOut) { throw 'Verification output already exists' }
New-Item -ItemType Directory -Path $ecfOut -Force | Out-Null
$env:PYTHONDONTWRITEBYTECODE='1'
$ecfSummary=[ordered]@{started_at=[DateTimeOffset]::UtcNow.ToString('o');uid=$Uid;firmware_sha256=$ecfRelease.firmware.sha256;passed=$false;steps=@()}
function Run-EcfPython([string]$EcfScript,[string[]]$EcfToolArguments) {
    & $Python (Join-Path $ecfRoot "tools/$EcfScript") @EcfToolArguments
    if ($LASTEXITCODE -ne 0) { throw "$EcfScript failed; evidence is retained in $ecfOut" }
    $ecfSummary.steps+= $EcfScript
}
function Convert-EcfWslPath([string]$EcfPath) {
    $ecfWslArguments=@()
    if ($WslDistribution) { $ecfWslArguments+=@('-d',$WslDistribution) }
    $ecfWslArguments+=@('--exec','wslpath','-a',[IO.Path]::GetFullPath($EcfPath))
    $ecfLinuxPath=(& wsl.exe @ecfWslArguments 2> (Join-Path $ecfOut 'wslpath-stderr.txt'))
    if ($LASTEXITCODE -ne 0 -or !$ecfLinuxPath) { throw 'WSL path conversion failed' }
    return ($ecfLinuxPath -join "`n").Trim()
}
try {
    Run-EcfPython 'check_source.py' @('--source',(Join-Path $ecfRoot 'source'),'--manifest',(Join-Path $ecfRoot $ecfRelease.source_manifest))
    if ($FlashFactoryImage) {
        Run-EcfPython 'cli_batch.py' @('--port',$Port,'--uid',$Uid,'--out',(Join-Path $ecfOut 'before-flash-config.json'),'--cmd','status','--cmd','dump all','--cmd','exit')
        Run-EcfPython 'flash_swd.py' @('--image',$ecfImage,'--sha256',$ecfRelease.firmware.sha256,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--backup',(Join-Path $ecfOut 'before-flash-full.bin'),'--out',(Join-Path $ecfOut 'flash.json'),'--cube',$Cube)
    }
    Run-EcfPython 'identify.py' @('--port',$Port,'--uid',$Uid,'--variant','INAV','--version-hex','090100','--out',(Join-Path $ecfOut 'identity.json'))
    Run-EcfPython 'readback_verify.py' @('--image',$ecfImage,'--sha256',$ecfRelease.firmware.sha256,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--backup',(Join-Path $ecfOut 'readback-full.bin'),'--out',(Join-Path $ecfOut 'readback.json'),'--cube',$Cube)
    Run-EcfPython 'cli_batch.py' @('--port',$Port,'--uid',$Uid,'--out',(Join-Path $ecfOut 'config-before-tests.json'),'--cmd','status','--cmd','dump all','--cmd','exit')
    Run-EcfPython 'verify_factory_defaults.py' @('--root',$ecfRoot,'--port',$Port,'--uid',$Uid,'--out',(Join-Path $ecfOut 'factory-defaults.json'))
    Run-EcfPython 'verify_led.py' @('--port',$Port,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--cube',$Cube,'--out',(Join-Path $ecfOut 'led-polarity.json'))
    Run-EcfPython 'verify_outputs.py' @('--port',$Port,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--cube',$Cube,'--unloaded-bench','--out',(Join-Path $ecfOut 'outputs.json'))
    foreach ($ecfPhase in @('serial','shared')) {
        Run-EcfPython 'verify_interfaces.py' @('--phase',$ecfPhase,'--port',$Port,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--cube',$Cube,'--out',(Join-Path $ecfOut "$ecfPhase.json"))
    }
    Run-EcfPython 'verify_can.py' @('--port',$Port,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--cube',$Cube,'--unloaded-bench','--normal-config','--out',(Join-Path $ecfOut 'can.json'))
    Run-EcfPython 'probe_sensors.py' @('--port',$Port,'--uid',$Uid,'--expect-missing-gps','--out',(Join-Path $ecfOut 'sensors-120s.json'))
    Run-EcfPython 'verify_reboots.py' @('--port',$Port,'--uid',$Uid,'--out',(Join-Path $ecfOut 'ordinary-reboots.json'))
    $ecfMscBase=Join-Path $ecfOut 'storage-baseline.json'
    Run-EcfPython 'msc_roundtrip.py' @('--port',$Port,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--cube',$Cube,'--variant','INAV','--write-probe','--hash-existing-files','--out',$ecfMscBase)
    foreach ($ecfCycle in @(2,3)) {
        Run-EcfPython 'msc_roundtrip.py' @('--port',$Port,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--cube',$Cube,'--variant','INAV','--write-probe','--out',(Join-Path $ecfOut "storage-cycle$ecfCycle.json"))
    }
    Run-EcfPython 'cli_batch.py' @('--port',$Port,'--uid',$Uid,'--out',(Join-Path $ecfOut 'log-prepare.json'),'--cmd','feature BLACKBOX','--cmd','set blackbox_arm_control = 0','--cmd','set blackbox_rate_num = 1','--cmd','set blackbox_rate_denom = 1','--cmd','sd_info','--cmd','save')
    Run-EcfPython 'record_blackbox.py' @('--port',$Port,'--uid',$Uid,'--unloaded-bench','--expect-missing-gps','--seconds','35','--out',(Join-Path $ecfOut 'log-record.json'))
    $ecfDiagnostics=Join-Path $ecfOut 'log-after-stop.json'
    Run-EcfPython 'cli_batch.py' @('--port',$Port,'--uid',$Uid,'--out',$ecfDiagnostics,'--cmd','status','--cmd','sd_info','--cmd','get blackbox_arm_control','--cmd','exit')
    $ecfLog=Join-Path $ecfOut 'INAV-STABLE.TXT'
    Run-EcfPython 'msc_roundtrip.py' @('--port',$Port,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--cube',$Cube,'--variant','INAV','--baseline',$ecfMscBase,'--copy-log',$ecfLog,'--log-extension','.TXT','--hash-existing-files','--out',(Join-Path $ecfOut 'log-download.json'))
    $ecfBeforeStorage=Get-Content -LiteralPath $ecfMscBase -Raw | ConvertFrom-Json
    $ecfAfterStorage=Get-Content -LiteralPath (Join-Path $ecfOut 'log-download.json') -Raw | ConvertFrom-Json
    foreach ($ecfOldPayload in $ecfBeforeStorage.existing_content_sha256_before) {
        $ecfSamePayload=@($ecfAfterStorage.existing_content_sha256_after | Where-Object path -eq $ecfOldPayload.path)
        if ($ecfSamePayload.Count -ne 1 -or $ecfSamePayload[0].bytes -ne $ecfOldPayload.bytes -or $ecfSamePayload[0].sha256 -ne $ecfOldPayload.sha256) { throw "Existing SD payload changed: $($ecfOldPayload.path)" }
    }
    $ecfValidatorLinux=Convert-EcfWslPath (Join-Path $ecfRoot 'tools/validate_blackbox.py')
    $ecfLogLinux=Convert-EcfWslPath $ecfLog
    $ecfDiagnosticsLinux=Convert-EcfWslPath $ecfDiagnostics
    $ecfDecoderLinux=Convert-EcfWslPath (Join-Path $ecfRoot 'tools/blackbox')
    $ecfPrefixLinux=Convert-EcfWslPath (Join-Path $ecfOut 'log')
    $ecfWslArguments=@()
    if ($WslDistribution) { $ecfWslArguments+=@('-d',$WslDistribution) }
    $ecfWslArguments+=@('--exec','python3',$ecfValidatorLinux,'--input',$ecfLogLinux,'--diagnostics',$ecfDiagnosticsLinux,'--tools-root',$ecfDecoderLinux,'--output-prefix',$ecfPrefixLinux,'--negative-control')
    & wsl.exe @ecfWslArguments *> (Join-Path $ecfOut 'decoder-console.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Native log decoder validation failed' }
    $ecfSummary.steps+='official-native-log-validation'
    Run-EcfPython 'cli_batch.py' @('--port',$Port,'--uid',$Uid,'--out',(Join-Path $ecfOut 'restore-defaults.json'),'--cmd','defaults')
    Run-EcfPython 'verify_rom.py' @('--port',$Port,'--uid',$Uid,'--stlink-sn',$StlinkSerial,'--cube',$Cube,'--elf',$ecfElf,'--out',(Join-Path $ecfOut 'rom-dfu.json'))
    Run-EcfPython 'probe_sensors.py' @('--port',$Port,'--uid',$Uid,'--seconds','5','--expect-missing-gps','--out',(Join-Path $ecfOut 'final-smoke.json'))
    $ecfSummary.passed=$true
} finally {
    $ecfSummary.finished_at=[DateTimeOffset]::UtcNow.ToString('o')
    $ecfSummary | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $ecfOut 'result.json') -Encoding utf8
}
Write-Output "INAV automated verification passed: $ecfOut"
