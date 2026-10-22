$ErrorActionPreference = "Stop"
$productRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $productRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Mirally Python environment not found at $python. Create .venv in the repository root first (see README.md)."
}

Push-Location $productRoot
try {
    & $python -m mirally @args
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exitCode
