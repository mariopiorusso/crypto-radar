$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Create .venv and install requirements-lock.txt before running a scan." }
if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
& .\.venv\Scripts\python.exe -m crypto_radar.main --once
exit $LASTEXITCODE
