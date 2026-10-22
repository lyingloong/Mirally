param(
    [ValidateSet('Release', 'Debug')]
    [string]$Configuration = 'Release',
    [string]$VisualStudioPath,
    [string]$MsvcVersion,
    [string]$WindowsSdkRoot,
    [string]$WindowsSdkVersion
)

$ErrorActionPreference = 'Stop'

function Get-VersionDirectories([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Container) {
        Get-ChildItem -LiteralPath $Path -Directory |
            Where-Object { $_.Name -match '^\d+(\.\d+){1,3}$' } |
            Sort-Object { [version]$_.Name } -Descending
    }
}

function Test-WindowsSdk([string]$Root, [string]$Version) {
    $requiredFiles = @(
        "Include/$Version/cppwinrt/winrt/Windows.Graphics.Capture.h",
        "Include/$Version/winrt/windows.graphics.capture.h",
        "Include/$Version/um/windows.graphics.capture.interop.h",
        "Include/$Version/shared/sdkddkver.h",
        "Include/$Version/ucrt/stdio.h",
        "Lib/$Version/um/x64/windowsapp.lib",
        "Lib/$Version/ucrt/x64/ucrt.lib"
    )
    foreach ($relativePath in $requiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $Root $relativePath) -PathType Leaf)) {
            return $false
        }
    }
    return $true
}

if (-not $VisualStudioPath) {
    if ($env:VSINSTALLDIR) {
        $VisualStudioPath = $env:VSINSTALLDIR
    } else {
        $vswherePath = $null
        if (${env:ProgramFiles(x86)}) {
            $candidate = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $vswherePath = $candidate }
        }
        if (-not $vswherePath) {
            $vswhereCommand = Get-Command vswhere.exe -ErrorAction SilentlyContinue
            if ($vswhereCommand) { $vswherePath = $vswhereCommand.Source }
        }
        if (-not $vswherePath) {
            throw 'Visual Studio discovery is unavailable. Install Visual Studio or Build Tools with Desktop development with C++, or pass -VisualStudioPath.'
        }
        $installation = & $vswherePath -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($LASTEXITCODE -ne 0 -or -not $installation) {
            throw 'No Visual Studio C++ toolchain found. In Visual Studio Installer, install Desktop development with C++ (MSVC x64/x86 build tools).'
        }
        $VisualStudioPath = ([string]$installation).Trim()
    }
}

$toolsetsRoot = Join-Path $VisualStudioPath 'VC/Tools/MSVC'
if ($MsvcVersion) {
    $toolRoot = Join-Path $toolsetsRoot $MsvcVersion
} else {
    $toolset = Get-VersionDirectories $toolsetsRoot | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName 'bin/Hostx64/x64/cl.exe') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName 'include/vector') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName 'lib/x64') -PathType Container)
    } | Select-Object -First 1
    if (-not $toolset) {
        throw "No usable MSVC x64 toolchain under '$toolsetsRoot'. Install MSVC x64/x86 build tools through Visual Studio Installer, or check -VisualStudioPath/-MsvcVersion."
    }
    $toolRoot = $toolset.FullName
    $MsvcVersion = $toolset.Name
}
$compiler = Join-Path $toolRoot 'bin/Hostx64/x64/cl.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw "MSVC compiler not found at '$compiler'. Check -VisualStudioPath/-MsvcVersion or install the MSVC x64/x86 build tools."
}

if (-not $WindowsSdkRoot) {
    $sdkRoots = @()
    if ($env:WindowsSdkDir) { $sdkRoots += $env:WindowsSdkDir }
    foreach ($registryPath in @(
        'HKLM:/SOFTWARE/Microsoft/Windows Kits/Installed Roots',
        'HKLM:/SOFTWARE/Wow6432Node/Microsoft/Windows Kits/Installed Roots'
    )) {
        $installedRoots = Get-ItemProperty -LiteralPath $registryPath -ErrorAction SilentlyContinue
        if ($installedRoots -and $installedRoots.KitsRoot10) { $sdkRoots += $installedRoots.KitsRoot10 }
    }
    foreach ($candidateRoot in ($sdkRoots | Select-Object -Unique)) {
        $versions = if ($WindowsSdkVersion) { @($WindowsSdkVersion) } else {
            @(Get-VersionDirectories (Join-Path $candidateRoot 'Include') | Select-Object -ExpandProperty Name)
        }
        foreach ($candidateVersion in $versions) {
            if (Test-WindowsSdk $candidateRoot $candidateVersion) {
                $WindowsSdkRoot = $candidateRoot
                $WindowsSdkVersion = $candidateVersion
                break
            }
        }
        if ($WindowsSdkRoot) { break }
    }
} elseif (-not $WindowsSdkVersion) {
    $sdk = Get-VersionDirectories (Join-Path $WindowsSdkRoot 'Include') | Where-Object {
        Test-WindowsSdk $WindowsSdkRoot $_.Name
    } | Select-Object -First 1
    if ($sdk) { $WindowsSdkVersion = $sdk.Name }
}
if (-not $WindowsSdkRoot -or -not $WindowsSdkVersion -or
    -not (Test-WindowsSdk $WindowsSdkRoot $WindowsSdkVersion)) {
    throw 'No complete Windows SDK with C++/WinRT headers and x64 libraries found. Install a Windows 10/11 SDK through Visual Studio Installer, or check -WindowsSdkRoot/-WindowsSdkVersion.'
}

$sdkRoot = $WindowsSdkRoot
$sdkVersion = $WindowsSdkVersion
$source = Join-Path $PSScriptRoot '..\mirally\platform\activity\native\desktop_wgc.cpp'
$outputDir = Join-Path $PSScriptRoot '..\tools\bin'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$optimization = if ($Configuration -eq 'Debug') { '/Od' } else { '/O2' }
$argsList = @(
    '/nologo', '/std:c++17', '/EHsc', $optimization,
    "/I$toolRoot/include", "/I$sdkRoot/Include/$sdkVersion/cppwinrt",
    "/I$sdkRoot/Include/$sdkVersion/winrt", "/I$sdkRoot/Include/$sdkVersion/um",
    "/I$sdkRoot/Include/$sdkVersion/shared", "/I$sdkRoot/Include/$sdkVersion/ucrt",
    $source, "/Fe:$outputDir/desktop_wgc.exe", "/Fo:$outputDir/desktop_wgc.obj",
    '/link', "/LIBPATH:$toolRoot/lib/x64", "/LIBPATH:$sdkRoot/Lib/$sdkVersion/um/x64",
    "/LIBPATH:$sdkRoot/Lib/$sdkVersion/ucrt/x64", 'd3d11.lib', 'dxgi.lib',
    'windowsapp.lib', 'user32.lib', 'windowscodecs.lib', 'shlwapi.lib'
)
Write-Host "MSVC $MsvcVersion ($VisualStudioPath)"
Write-Host "Windows SDK $sdkVersion ($sdkRoot)"
& $compiler @argsList
if ($LASTEXITCODE) { throw "WGC compilation failed ($LASTEXITCODE)" }
Write-Host "Built $outputDir/desktop_wgc.exe"

