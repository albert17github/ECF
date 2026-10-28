param(
    [Parameter(Mandatory=$true)][string]$Port,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{24}$')][string]$Uid,
    [Parameter(Mandatory=$true)][string]$StlinkSerial,
    [string]$Python = 'python', [string]$Node = 'node',
    [string]$RunName = ('bf-release-' + (Get-Date -Format 'yyyyMMdd-HHmmss')),
    [switch]$UnloadedBench,
    [switch]$FlashFactoryImage
)
if (-not $UnloadedBench) { throw 'This verification requires a USB/ST-Link-only bench with no receiver, ESCs or actuators connected. Specify -UnloadedBench after establishing those conditions.' }
$ErrorActionPreference = 'Stop'
$ecfRoot = $PSScriptRoot
$ecfPython = $Python
$ecfRun = Join-Path $ecfRoot ('.build/verification/' + $RunName)
if (Test-Path -LiteralPath $ecfRun) { throw 'Validation run already exists; preserve its evidence and use a new run name.' }
New-Item -ItemType Directory -Path $ecfRun | Out-Null
$ecfCommon = @('--port', $Port, '--uid', $Uid)
$ecfSwd = @('--stlink-sn', $StlinkSerial)
function Invoke-EcfReleaseStep {
    param([string]$Name, [string]$Script, [string[]]$StepArguments)
    Write-Output $Name
    & $ecfPython (Join-Path $ecfRoot $Script) @StepArguments
    if ($LASTEXITCODE -ne 0) { throw "Board verification failed: $Name. Evidence retained at $ecfRun" }
}
$ecfRelease = Get-Content -Raw -LiteralPath (Join-Path $ecfRoot 'release.json') | ConvertFrom-Json
$ecfImage = Join-Path $ecfRoot $ecfRelease.firmware.image
if ($ecfRelease.firmware_build_pending -or $ecfRelease.firmware.source_manifest_sha256 -ne $ecfRelease.source_manifest_sha256) { throw 'Rebuild the matching source revision before verification' }
Invoke-EcfReleaseStep '完整源码校验' 'tools/check_source.py' @('--source',(Join-Path $ecfRoot 'source'),'--manifest',(Join-Path $ecfRoot $ecfRelease.source_manifest))

if ($FlashFactoryImage) {
    Invoke-EcfReleaseStep 'Back up and flash the factory image' 'tools/flash_swd.py' @('--image', $ecfImage, '--sha256', $ecfRelease.firmware.sha256, '--uid', $Uid, '--stlink-sn', $StlinkSerial, '--backup', (Join-Path $ecfRun 'before-flash-2MiB.bin'), '--out', (Join-Path $ecfRun 'flash.json'))
}
Invoke-EcfReleaseStep 'Readback image verification' 'tools/readback_verify.py' @('--image', $ecfImage, '--sha256', $ecfRelease.firmware.sha256, '--uid', $Uid, '--stlink-sn', $StlinkSerial, '--backup', (Join-Path $ecfRun 'preflight-flash-2MiB.bin'), '--out', (Join-Path $ecfRun 'readback.json'))
Invoke-EcfReleaseStep 'Application identity' 'tools/identify.py' ($ecfCommon + @('--variant', 'BTFL', '--version-hex', $ecfRelease.msp_version_hex, '--out', (Join-Path $ecfRun 'identity.json')))
Invoke-EcfReleaseStep 'Configuration backup' 'tools/cli_batch.py' ($ecfCommon + @('--out', (Join-Path $ecfRun 'configuration-before.json'), '--cmd', 'dump all', '--cmd', 'exit'))
Invoke-EcfReleaseStep '统一出厂接口与比例' 'tools/verify_factory_defaults.py' ($ecfCommon + @('--root',$ecfRoot,'--out',(Join-Path $ecfRun 'factory-defaults.json')))
Invoke-EcfReleaseStep '八输出四种协议组合' 'tools/verify_outputs.py' ($ecfCommon + $ecfSwd + @('--out', (Join-Path $ecfRun 'outputs.json')))
Invoke-EcfReleaseStep '共享焊盘、ADC与两路CAN' 'tools/verify_shared_interfaces.py' ($ecfCommon + $ecfSwd + @('--out', (Join-Path $ecfRun 'interfaces.json')))
Invoke-EcfReleaseStep '串口MSP v1/v2读写' 'tools/verify_serial.py' ($ecfCommon + @('--out', (Join-Path $ecfRun 'serial.json')))
Invoke-EcfReleaseStep '三次普通重启参数保持' 'tools/verify_reboots.py' @('--port', $Port, '--output', (Join-Path $ecfRun 'reboots.json'), '--min-initial-uptime', '16')
Invoke-EcfReleaseStep '恢复安全出厂默认' 'tools/cli_batch.py' ($ecfCommon + @('--out', (Join-Path $ecfRun 'defaults.json'), '--cmd', 'defaults'))
Invoke-EcfReleaseStep '原生实时调度统计配置' 'tools/cli_batch.py' ($ecfCommon + @('--out', (Join-Path $ecfRun 'realtime-config.json'), '--cmd', 'set debug_mode = TIMING_ACCURACY', '--cmd', 'get cpu_late', '--cmd', 'save'))
Invoke-EcfReleaseStep '原生实时保护30秒' 'tools/verify_realtime.py' ($ecfCommon + @('--out', (Join-Path $ecfRun 'realtime.json'), '--seconds', '30'))
Invoke-EcfReleaseStep '三传感器连续120秒' 'tools/probe_sensors.py' @('--port', $Port, '--out', (Join-Path $ecfRun 'sensors-120s.json'), '--seconds', '120', '--settle-seconds', '8', '--expected-uid', $Uid, '--require-realtime')
Invoke-EcfReleaseStep 'SD独立64KiB写读与清理' 'tools/msc_roundtrip.py' ($ecfCommon + $ecfSwd + @('--out', (Join-Path $ecfRun 'storage-64k.json'), '--write-probe', '--hash-existing-files'))
$ecfStorage = Get-Content -Raw -LiteralPath (Join-Path $ecfRun 'storage-64k.json') | ConvertFrom-Json
$ecfPool = @($ecfStorage.inventory | Where-Object path -eq 'FREESPAC.E')
if ($ecfPool.Count -ne 1 -or $ecfPool[0].bytes -lt 16777216) { throw 'AFatFS allocation pool is too small for the log test; back up and rebuild its allocation file using the documented handoff entry.' }
Invoke-EcfReleaseStep '原生Blackbox测试配置' 'tools/cli_batch.py' ($ecfCommon + @('--out', (Join-Path $ecfRun 'log-config.json'), '--cmd', 'set blackbox_device = SDCARD', '--cmd', 'set blackbox_mode = NORMAL', '--cmd', 'set blackbox_sample_rate = 1/4', '--cmd', 'save'))
Invoke-EcfReleaseStep '未解锁原生日志35秒' 'tools/record_blackbox.py' @('--port', $Port, '--out', (Join-Path $ecfRun 'log-capture.json'), '--seconds', '35', '--unloaded-bench', '--expected-uid', $Uid, '--settle-seconds', '8', '--require-realtime')
Invoke-EcfReleaseStep '原生日志写入计数' 'tools/cli_batch.py' ($ecfCommon + @('--out', (Join-Path $ecfRun 'log-write-statistics.json'), '--cmd', 'sd_info', '--cmd', 'exit'))
Invoke-EcfReleaseStep '完整日志读取、安全弹出与NRST返回' 'tools/msc_roundtrip.py' ($ecfCommon + $ecfSwd + @('--out', (Join-Path $ecfRun 'log-download.json'), '--baseline', (Join-Path $ecfRun 'storage-64k.json'), '--copy-log', (Join-Path $ecfRun 'BF-STABLE.BFL'), '--hash-existing-files'))
$ecfNode = $Node
& $ecfNode (Join-Path $ecfRoot 'tools/blackbox/verify_log.bundle.mjs') (Join-Path $ecfRun 'BF-STABLE.BFL') (Join-Path $ecfRun 'log-official') *> (Join-Path $ecfRun 'log-decoder-console.txt')
if ($LASTEXITCODE -ne 0) { throw 'Official Blackbox decoding or integrity validation failed' }
Invoke-EcfReleaseStep '恢复日志默认并保存最终参数' 'tools/cli_batch.py' ($ecfCommon + @('--out', (Join-Path $ecfRun 'final-config.json'), '--cmd', 'set blackbox_mode = NORMAL', '--cmd', 'set debug_mode = NONE', '--cmd', 'save'))
Invoke-EcfReleaseStep '最终身份、接口与运行状态' 'tools/cli_batch.py' ($ecfCommon + @('--out', (Join-Path $ecfRun 'final-cli.json'), '--cmd', 'version', '--cmd', 'status', '--cmd', 'resource', '--cmd', 'resource show all', '--cmd', 'get gyro_2', '--cmd', 'get gyro_enabled', '--cmd', 'get blackbox', '--cmd', 'get ledstrip', '--cmd', 'led', '--cmd', 'diff all', '--cmd', 'exit'))
$ecfWriteDoc = Get-Content -Raw -LiteralPath (Join-Path $ecfRun 'log-write-statistics.json') | ConvertFrom-Json
$ecfWriteText = ($ecfWriteDoc.commands | Where-Object command -eq 'sd_info').response
if ($ecfWriteText -notmatch 'ECF_WRITE_REFUSED_BYTES=0(?:\r?\n)') { throw 'Native SD write refusal counter is not zero' }
$ecfAccepted = [regex]::Match($ecfWriteText, 'ECF_WRITE_ACCEPTED_BYTES=(\d+)').Groups[1].Value
$ecfDownload = Get-Content -Raw -LiteralPath (Join-Path $ecfRun 'log-download.json') | ConvertFrom-Json
if ([uint64]$ecfAccepted -ne [uint64]$ecfDownload.download.bytes) { throw 'Accepted native bytes do not equal the complete downloaded log length' }
foreach ($ecfOldPayload in $ecfStorage.existing_content_sha256_before) {
    $ecfSamePayload = @($ecfDownload.existing_content_sha256_after | Where-Object path -eq $ecfOldPayload.path)
    if ($ecfSamePayload.Count -ne 1 -or $ecfSamePayload[0].bytes -ne $ecfOldPayload.bytes -or $ecfSamePayload[0].sha256 -ne $ecfOldPayload.sha256) { throw "Existing SD payload changed: $($ecfOldPayload.path)" }
}
Invoke-EcfReleaseStep 'ROM DFU and hardware NRST' 'tools/verify_rom.py' ($ecfCommon + $ecfSwd + @('--elf', (Join-Path $ecfRoot $ecfRelease.firmware.elf), '--out', (Join-Path $ecfRun 'rom-nrst.json'), '--cycles', '3'))
$ecfSummary = @{run=$RunName;directory=$ecfRun;completed_at=(Get-Date).ToString('o');automatic_sequence_completed=$true;physical_waveforms_verified=$false;flight_tested=$false}
$ecfSummary | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ecfRun 'sequence-summary.json') -Encoding utf8
Write-Output "BF release sequence completed: $ecfRun"
