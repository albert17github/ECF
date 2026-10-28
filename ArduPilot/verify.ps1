param(
    [Parameter(Mandatory=$true)][string]$Uid,
    [Parameter(Mandatory=$true)][string]$StlinkSerial,
    [Parameter(Mandatory=$true)][switch]$UnloadedBench,
    [string]$Port='auto',
    [string]$Python='python',
    [string]$Cube=$env:ECF_CUBE_PROGRAMMER,
    [string]$OutputDirectory,
    [string]$ContinueRecord,
    [switch]$FlashFactoryImage
)
$ErrorActionPreference='Stop'
if (!$UnloadedBench -or $Uid -notmatch '^[0-9a-fA-F]{24}$') { throw 'Specify the unloaded bench and 12-byte MCU UID' }
if (!$Cube) { $Cube='C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe' }
$Cube=(Resolve-Path -LiteralPath $Cube).Path
$ecfArguments=@('--uid',$Uid,'--stlink-sn',$StlinkSerial,'--port',$Port,'--unloaded-bench','--cube',$Cube)
if ($OutputDirectory) { $ecfArguments+=@('--out',$OutputDirectory) }
if ($ContinueRecord) { $ecfArguments+=@('--continue-record',$ContinueRecord) }
if ($FlashFactoryImage) { $ecfArguments+='--flash-factory-image' }
& $Python (Join-Path $PSScriptRoot 'tools/run_arducopter_verification.py') @ecfArguments
if ($LASTEXITCODE -ne 0) { throw 'ArduCopter verification failed; its output directory retains the evidence' }
