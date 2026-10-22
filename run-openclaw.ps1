$ErrorActionPreference = "Stop"
$productRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeFile = Join-Path $productRoot ".artifacts\openclaw-runtime.json"
if (-not (Test-Path -LiteralPath $runtimeFile)) {
    throw "OpenClaw runtime missing. Run .\.venv\Scripts\python.exe tools\setup_openclaw.py first."
}
$runtime = Get-Content -Raw -LiteralPath $runtimeFile | ConvertFrom-Json
if ($args.Count -gt 0 -and $args[0] -eq "companion") {
    throw "The old companion interface has been removed. Use .\run-openclaw.ps1 mirally."
}
if ($args.Count -gt 0 -and $args[0] -eq "mirally") {
    $python = Join-Path $productRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Mirally Python environment missing. See README.md."
    }
    Push-Location $productRoot
    try {
        & $python -m mirally --openclaw
        $exitCode = $LASTEXITCODE
    }
    finally { Pop-Location }
    exit $exitCode
}
$node = Join-Path $productRoot $runtime.node
$entry = Join-Path $productRoot $runtime.entry
$stateRoot = Join-Path $productRoot "config\mirally\openclaw"
$savedState = $env:OPENCLAW_STATE_DIR
$savedConfig = $env:OPENCLAW_CONFIG_PATH
$savedPath = $env:PATH
Push-Location $productRoot
try {
    $env:OPENCLAW_STATE_DIR = $stateRoot
    $env:OPENCLAW_CONFIG_PATH = Join-Path $stateRoot "openclaw.json"
    $env:PATH = (Split-Path -Parent $node) + [IO.Path]::PathSeparator + $savedPath
    & $node $entry @args
    $exitCode = $LASTEXITCODE
}
finally {
    $env:OPENCLAW_STATE_DIR = $savedState
    $env:OPENCLAW_CONFIG_PATH = $savedConfig
    $env:PATH = $savedPath
    Pop-Location
}
exit $exitCode
