param([ValidateSet('v11','v12','all')][string]$Detector = 'v11')
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) { throw 'Create .venv and install requirements-lock.txt first.' }
& .\.venv\Scripts\python.exe -B -m crypto_radar.main --detector $Detector
exit $LASTEXITCODE
