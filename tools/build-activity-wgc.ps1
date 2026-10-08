param(
    [string]$Configuration = "Release"
)

$toolRoot = 'C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Tools/MSVC/14.44.35207'
$sdkRoot = 'C:/Program Files (x86)/Windows Kits/10'
$sdkVersion = '10.0.26100.0'
$source = Join-Path $PSScriptRoot '..\openassist\platform\activity\native\desktop_wgc.cpp'
$outputDir = Join-Path $PSScriptRoot '..\tools\bin'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$argsList = @(
    '/nologo', '/std:c++17', '/EHsc', '/O2',
    "/I$toolRoot/include", "/I$sdkRoot/Include/$sdkVersion/cppwinrt",
    "/I$sdkRoot/Include/$sdkVersion/winrt", "/I$sdkRoot/Include/$sdkVersion/um",
    "/I$sdkRoot/Include/$sdkVersion/shared", "/I$sdkRoot/Include/$sdkVersion/ucrt",
    $source, "/Fe:$outputDir/desktop_wgc.exe", "/Fo:$outputDir/desktop_wgc.obj",
    '/link', "/LIBPATH:$toolRoot/lib/x64", "/LIBPATH:$sdkRoot/Lib/$sdkVersion/um/x64",
    "/LIBPATH:$sdkRoot/Lib/$sdkVersion/ucrt/x64", 'd3d11.lib', 'dxgi.lib',
    'windowsapp.lib', 'user32.lib', 'windowscodecs.lib', 'shlwapi.lib'
)
& "$toolRoot/bin/Hostx64/x64/cl.exe" @argsList
if ($LASTEXITCODE) { throw "WGC compilation failed ($LASTEXITCODE)" }
Write-Host "Built $outputDir/desktop_wgc.exe"

