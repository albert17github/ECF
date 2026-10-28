param(
    [Parameter(Mandatory=$true)][string]$WslDistribution,
    [string]$LinuxPython = 'python3', [string]$ArmBin = '/usr/bin',
    [ValidateRange(1,64)][int]$Jobs = 8
)
$ErrorActionPreference = 'Stop'
$ecfLinuxRoot = & wsl.exe -d $WslDistribution --exec wslpath -u $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve this project in the selected Linux distribution' }
$ecfLinuxRoot = $ecfLinuxRoot.Trim()
& wsl.exe -d $WslDistribution --exec env "ECF_PYTHON=$LinuxPython" bash "$ecfLinuxRoot/build.sh" --arm-bin $ArmBin --jobs $Jobs
if ($LASTEXITCODE -ne 0) { throw 'PX4 build failed; inspect the new .build record' }
