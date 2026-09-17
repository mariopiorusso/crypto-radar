$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$relayPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $relayPython -B -m crypto_radar.mail_relay --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$relayProcess = Start-Process -FilePath $relayPython -ArgumentList "-B", "-m", "crypto_radar.mail_relay" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
$relayProcess.Refresh()
if ($relayProcess.HasExited) { throw "Relay exited. Check logs/mail-relay.log; another relay may already be running." }
Write-Output "Local relay started (PID $($relayProcess.Id)). Logs: logs/mail-relay.log"
