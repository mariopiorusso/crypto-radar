param([ValidateRange(1,60)][int]$EveryMinutes = 1)
$ErrorActionPreference = 'Stop'
$workerRoot = $PSScriptRoot
$workerPython = Join-Path $workerRoot '.venv\Scripts\pythonw.exe'
foreach ($workerFile in @($workerPython, (Join-Path $workerRoot 'config.json'), (Join-Path $workerRoot 'token.json'))) {
    if (-not (Test-Path -LiteralPath $workerFile)) { throw "Complete setup first; missing $workerFile" }
}
# A separate, limited interactive-user task uses that user's existing Codex login.
# This does not start, stop or update any Crypto Radar scanner/relay/report task.
$workerName = 'Crypto Radar - Handshake Poller'
$workerIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$workerPrincipal = New-ScheduledTaskPrincipal -UserId $workerIdentity -LogonType Interactive -RunLevel Limited
$workerAction = New-ScheduledTaskAction -Execute $workerPython `
    -Argument ('-B "' + (Join-Path $workerRoot 'gmail_poller.py') + '" --config "' + (Join-Path $workerRoot 'config.json') + '"') `
    -WorkingDirectory $workerRoot
$workerTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$workerSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $workerName -Action $workerAction -Trigger $workerTrigger `
    -Principal $workerPrincipal -Settings $workerSettings `
    -Description 'Deterministic Gmail polling; fixed read-only Codex handshake only when accepted work exists.' -Force | Out-Null
Write-Output "Installed $workerName. Polls every $EveryMinutes minutes while this Windows user is signed in."
