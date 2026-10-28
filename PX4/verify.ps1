param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-fA-F]{24}$')][string]$Uid,
    [Parameter(Mandatory=$true)][string]$StlinkSerial,
    [Parameter(Mandatory=$true)][switch]$UnloadedBench,
    [string]$Port='auto', [string]$Python='python', [string]$Cube=$env:ECF_CUBE_PROGRAMMER,
    [switch]$FlashFactoryImage, [switch]$FactoryResetParameters, [string]$ContinueRecord, [string]$SdBaselineRecord, [string]$CompletedLogRecord, [switch]$ReuseCompletedTail
)
$ErrorActionPreference='Stop'
if (-not $UnloadedBench) { throw 'An unloaded USB/ST-Link bench is required' }
$ecfArgs=@('--uid',$Uid,'--stlink-sn',$StlinkSerial,'--port',$Port,'--unloaded-bench')
if ($Cube) { $ecfArgs+=@('--cube',$Cube) }
if ($FlashFactoryImage) { $ecfArgs+='--flash-factory-image' }
if ($FactoryResetParameters) { $ecfArgs+='--factory-reset-parameters' }
if ($ContinueRecord) { $ecfArgs+=@('--continue-record',$ContinueRecord) }
if ($SdBaselineRecord) { $ecfArgs+=@('--sd-baseline-record',$SdBaselineRecord) }
if ($CompletedLogRecord) { $ecfArgs+=@('--completed-log-record',$CompletedLogRecord) }
if ($ReuseCompletedTail) { $ecfArgs+='--reuse-completed-tail' }
$env:PYTHONDONTWRITEBYTECODE='1'
& $Python (Join-Path $PSScriptRoot 'tools/run_verification.py') @ecfArgs
if ($LASTEXITCODE -ne 0) { throw 'PX4 verification failed; preserve its .build/verification record' }
