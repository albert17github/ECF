param(
    [string]$ArmBin = $env:ECF_ARM_GCC_BIN,
    [string]$CMake = $env:ECF_CMAKE,
    [string]$Ruby = $env:ECF_RUBY,
    [string]$Ninja = $env:ECF_NINJA,
    [string]$Python = 'python',
    [int]$Jobs = 8,
    [string]$BuildDirectory,
    [switch]$AllowModifiedSource
)
$ErrorActionPreference = 'Stop'
function Resolve-EcfTool([string]$Candidate, [string]$DefaultName) {
    if (!$Candidate) { $Candidate = $DefaultName }
    if (Test-Path -LiteralPath $Candidate -PathType Leaf) { return (Resolve-Path -LiteralPath $Candidate).Path }
    return (Get-Command -Name $Candidate -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}
$ecfRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$ecfSource = (Resolve-Path -LiteralPath (Join-Path $ecfRoot 'source')).Path
$ecfRelease = Get-Content -LiteralPath (Join-Path $ecfRoot 'release.json') -Raw | ConvertFrom-Json
if (!$ArmBin) { $ArmBin = Split-Path -Parent (Resolve-EcfTool '' 'arm-none-eabi-gcc.exe') }
$ArmBin = (Resolve-Path -LiteralPath $ArmBin).Path
$ecfCompiler = Resolve-EcfTool (Join-Path $ArmBin 'arm-none-eabi-gcc.exe') ''
$CMake = Resolve-EcfTool $CMake 'cmake.exe'
$Ruby = Resolve-EcfTool $Ruby 'ruby.exe'
$Ninja = Resolve-EcfTool $Ninja 'ninja.exe'
$ecfVersion = (& $ecfCompiler -dumpfullversion).Trim()
if ($LASTEXITCODE -ne 0 -or $ecfVersion -ne '13.3.1') { throw "Expected ARM GCC 13.3.1; received $ecfVersion" }
if (!$AllowModifiedSource) {
    $ecfManifest = Join-Path $ecfRoot $ecfRelease.source_manifest
    if ((Get-FileHash -LiteralPath $ecfManifest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ecfRelease.source_manifest_sha256) { throw 'Source manifest digest mismatch' }
    & $Python (Join-Path $ecfRoot 'tools/check_source.py') --source $ecfSource --manifest $ecfManifest
    if ($LASTEXITCODE -ne 0) { throw 'Release source verification failed' }
}
$ecfStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$ecfBuildRoot = [IO.Path]::GetFullPath((Join-Path $ecfRoot '.build'))
if (!$BuildDirectory) { $BuildDirectory = Join-Path $ecfBuildRoot "compile-$ecfStamp" }
$BuildDirectory = [IO.Path]::GetFullPath($BuildDirectory)
if (!$BuildDirectory.StartsWith($ecfBuildRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $BuildDirectory)) { throw 'Choose a new build directory inside .build/' }
$ecfOutput = Join-Path $ecfBuildRoot "artifacts/$ecfStamp"
if (Test-Path -LiteralPath $ecfOutput) { throw 'Artifact output already exists' }
New-Item -ItemType Directory -Path $ecfOutput -Force | Out-Null
$env:PATH = "$ArmBin;$(Split-Path -Parent $CMake);$(Split-Path -Parent $Ruby);$(Split-Path -Parent $Ninja);$env:PATH"
$env:SOURCE_DATE_EPOCH = [string]$ecfRelease.source_date_epoch
$ecfSourceMapped = $ecfSource.Replace('\','/')
$ecfRootMapped = $ecfRoot.Replace('\','/')
$ecfBuildMapped = $BuildDirectory.Replace('\','/')
$ecfToolchainMapped = (Split-Path -Parent $ArmBin).Replace('\','/')
$ecfPrefixMaps = '-ffile-prefix-map="'+$ecfRootMapped+'=/ecf" -fdebug-prefix-map="'+$ecfRootMapped+'=/ecf" -ffile-prefix-map="'+$ecfBuildMapped+'=/ecf/build" -fdebug-prefix-map="'+$ecfBuildMapped+'=/ecf/build" -ffile-prefix-map="'+$ecfToolchainMapped+'=/ecf/toolchain" -fdebug-prefix-map="'+$ecfToolchainMapped+'=/ecf/toolchain"'
$ecfConfigureArgs = @('-S',$ecfSource,'-B',$BuildDirectory,'-G','Ninja',
    '-DCOMPILER_VERSION_CHECK=OFF','-DCMAKE_BUILD_TYPE=RelWithDebInfo','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',
    '-DINAV_TARGET_DIRS=ECFH743',"-DECF_UPSTREAM_COMMIT=$($ecfRelease.commit)",'-DIS_RELEASE_BUILD=ON',
    "-DRUBY_EXECUTABLE=$Ruby","-DCMAKE_MAKE_PROGRAM=$Ninja", "-DCMAKE_C_FLAGS=$ecfPrefixMaps",
    "-DCMAKE_CXX_FLAGS=$ecfPrefixMaps","-DCMAKE_ASM_FLAGS=$ecfPrefixMaps")
& $CMake @ecfConfigureArgs *> (Join-Path $ecfOutput 'configure.log')
if ($LASTEXITCODE -ne 0) { throw "Configure failed; see $ecfOutput/configure.log" }
& $CMake --build $BuildDirectory --target ECFH743 -- -j $Jobs *> (Join-Path $ecfOutput 'build.log')
if ($LASTEXITCODE -ne 0) { throw "Build failed; see $ecfOutput/build.log" }
& $Python (Join-Path $ecfRoot 'tools/collect_build.py') --build $BuildDirectory --output $ecfOutput --arm-bin $ArmBin --release (Join-Path $ecfRoot 'release.json')
if ($LASTEXITCODE -ne 0) { throw 'Artifact address/digest checks failed' }
New-Item -ItemType Directory -Path (Join-Path $ecfBuildRoot 'artifacts') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $ecfBuildRoot 'artifacts/LATEST.txt') -Value $ecfStamp -Encoding ascii
Write-Output "INAV clean build passed: $ecfOutput"
