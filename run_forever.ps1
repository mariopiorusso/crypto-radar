$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe -m crypto_radar.main
exit $LASTEXITCODE
