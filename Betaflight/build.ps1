param(
    [string]$ArmBin = $env:ECF_ARM_GCC_BIN,
    [string]$MakeBin = $env:ECF_MAKE_BIN,
    [string]$GitBash = $env:ECF_GIT_BASH,
    [string]$Python = 'python',
    [int]$Jobs = 8,
    [switch]$AllowModifiedSource
)
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'source')).Path
$release = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'release.json') | ConvertFrom-Json
if (-not $ArmBin) { $ArmBin = Split-Path -Parent (Get-Command arm-none-eabi-gcc.exe -ErrorAction Stop).Source }
if (-not $MakeBin) { $MakeBin = Split-Path -Parent (Get-Command make.exe -ErrorAction Stop).Source }
if (-not $GitBash) { $GitBash = (Get-Command bash.exe -ErrorAction Stop).Source }
$ArmBin = (Resolve-Path -LiteralPath $ArmBin).Path
$MakeBin = (Resolve-Path -LiteralPath $MakeBin).Path
$GitBash = (Resolve-Path -LiteralPath $GitBash).Path
$compiler = Join-Path $ArmBin 'arm-none-eabi-gcc.exe'
$version = (& $compiler -dumpfullversion).Trim()
if ($LASTEXITCODE -ne 0 -or $version -ne '13.3.1') { throw "Expected ARM GCC 13.3.1; received $version" }
if (-not $AllowModifiedSource) {
    & $Python (Join-Path $PSScriptRoot 'tools/check_source.py') --source $source --manifest (Join-Path $PSScriptRoot 'verification/source-SHA256SUMS.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Release source verification failed. Use -AllowModifiedSource for a changed fork.' }
}
$buildRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '.build'))
$rootPrefix = [IO.Path]::GetFullPath($PSScriptRoot) + [IO.Path]::DirectorySeparatorChar
if (-not $buildRoot.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Build output escaped the project directory' }
$ecfObjectRoot = [IO.Path]::GetFullPath((Join-Path $source 'obj'))
if (-not $ecfObjectRoot.StartsWith($source + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected native cleanup directory' }
if ((Test-Path -LiteralPath $ecfObjectRoot) -and ((Get-Item -LiteralPath $ecfObjectRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Native cleanup directory must not be a junction or symbolic link' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$output = Join-Path $buildRoot "artifacts/$stamp"
if (Test-Path -LiteralPath $output) { throw 'Build output already exists' }
New-Item -ItemType Directory -Force -Path $output | Out-Null
$env:SOURCE_DATE_EPOCH = [string]$release.source_date_epoch
$env:ECF_BF_SOURCE = $source
$env:ECF_BF_ARM_BIN = $ArmBin
$env:ECF_BF_MAKE_BIN = $MakeBin
$env:ECF_BF_JOBS = $Jobs
$script = @'
set -e
export PATH="/usr/bin:$(cygpath -u "$ECF_BF_MAKE_BIN"):$(cygpath -u "$ECF_BF_ARM_BIN"):$PATH"
cd "$(cygpath -u "$ECF_BF_SOURCE")"
common=(CONFIG=ECFH743 REVISION=e0b7bb01 CONFIG_REVISION=3a406c5 AUTOHYDRATE_STAMPS= GCC_REQUIRED_VERSION=13.3.1 ARM_SDK_PREFIX=arm-none-eabi-)
source_native=$(cygpath -m "$ECF_BF_SOURCE")
toolchain_native=$(cygpath -m "$ECF_BF_ARM_BIN/..")
prefix_flags="-ffile-prefix-map=\"$source_native\"=/ecf/source -fdebug-prefix-map=\"$source_native\"=/ecf/source -ffile-prefix-map=\"$toolchain_native\"=/ecf/toolchain -fdebug-prefix-map=\"$toolchain_native\"=/ecf/toolchain"
common+=("EXTRA_FLAGS=$prefix_flags" "ECF_DEBUG_PREFIX_FLAGS=$prefix_flags")
make "${common[@]}" clean
make "${common[@]}" -j"$ECF_BF_JOBS" hex
'@
& $GitBash -c $script *> (Join-Path $output 'build.log')
if ($LASTEXITCODE -ne 0) { throw "Build failed. See $output/build.log" }
$objects = Join-Path $source 'obj'
foreach ($extension in @('hex', 'elf', 'map')) {
    $matches = @(Get-ChildItem -LiteralPath $objects -Recurse -File -Filter "*ECFH743.$extension")
    if ($matches.Count -ne 1) { throw "Expected one $extension artifact, found $($matches.Count)" }
    Copy-Item -LiteralPath $matches[0].FullName -Destination (Join-Path $output "ECFH743.$extension")
}
& (Join-Path $ArmBin 'arm-none-eabi-objcopy.exe') -O binary --gap-fill 0xff (Join-Path $output 'ECFH743.elf') (Join-Path $output 'ECFH743.bin')
if ($LASTEXITCODE -ne 0) { throw 'BIN generation failed' }
& (Join-Path $ArmBin 'arm-none-eabi-objcopy.exe') -O ihex --gap-fill 0xff (Join-Path $output 'ECFH743.elf') (Join-Path $output 'ECFH743-factory.hex')
if ($LASTEXITCODE -ne 0) { throw 'Factory HEX generation failed' }
$metadata = [ordered]@{
    project = 'Betaflight'; target = 'ECFH743'; upstream_tag = $release.tag; upstream_commit = $release.commit
    source_verification = $(if ($AllowModifiedSource) { 'modified-source-build' } else { 'release-snapshot-verified' })
    compiler = 'ARM GCC 13.3.1'; compiler_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $compiler).Hash.ToLowerInvariant()
    load_address = '0x08000000'; configuration_start = '0x08020000'; configuration_end_exclusive = '0x08040000'
    factory_image = 'ECFH743-factory.hex'; factory_image_resets_configuration = $true
    built_at = (Get-Date).ToString('o'); build = 'passed'; hardware_verification = 'pending'
}
$metadata | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $output 'manifest.json') -Encoding utf8
Get-ChildItem -LiteralPath $output -File | Where-Object Name -ne 'SHA256SUMS.txt' | Get-FileHash -Algorithm SHA256 | ForEach-Object { $_.Hash.ToLowerInvariant() + '  ' + [IO.Path]::GetFileName($_.Path) } | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding utf8
Set-Content -LiteralPath (Join-Path $buildRoot 'artifacts/LATEST.txt') -Value $stamp -Encoding ascii
Write-Output "Betaflight build passed: $output"
