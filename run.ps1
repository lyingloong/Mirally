param([switch]$Setup)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$productRoot = $PSScriptRoot
$venv = Join-Path $productRoot ".venv"
$python = Join-Path $productRoot ".venv\Scripts\python.exe"

function Test-SetupPython {
    param([string]$Executable, [string[]]$Options = @())

    try {
        & $Executable @Options -c "import sys; sys.exit(sys.version_info[:2] != (3, 10))" 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

if ($Setup -and $args.Count -gt 0) {
    throw "Use -Setup on its own. Set MIRALLY_NODE to choose a specific Node executable."
}

Push-Location -LiteralPath $productRoot
try {
    if ($Setup) {
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            $launcher = Get-Command py.exe -CommandType Application -TotalCount 1 -ErrorAction SilentlyContinue
            if ($launcher -and (Test-SetupPython -Executable $launcher.Source -Options @("-3.10"))) {
                & $launcher.Source -3.10 -m venv $venv
            }
            else {
                $bootstrap = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
                    Where-Object { Test-SetupPython -Executable $_.Source } | Select-Object -First 1
                if (-not $bootstrap) {
                    throw "Install Python 3.10 with the Windows Python Launcher, or add Python 3.10 to PATH, then rerun .\run.ps1 -Setup."
                }
                & $bootstrap.Source -m venv $venv
            }
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }

        if (-not (Test-SetupPython -Executable $python)) {
            throw "The local .venv needs a working Python 3.10. Move it aside and rerun .\run.ps1 -Setup."
        }
        & $python -m pip install -r requirements-framework.txt
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $python tools\setup_framework.py
    }
    else {
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw "Mirally's .venv is missing. Run .\run.ps1 -Setup from the repository root."
        }
        & $python -m mirally @args
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
