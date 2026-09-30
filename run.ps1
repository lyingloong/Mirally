$ErrorActionPreference = "Stop"
$productRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $productRoot "UFO\.venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "UFO Python environment not found at $python. Create the UFO environment first."
}

Push-Location $productRoot
try {
    & $python -m openassist @args
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exitCode
