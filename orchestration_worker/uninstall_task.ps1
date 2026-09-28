$ErrorActionPreference = 'Stop'
$workerName = 'Crypto Radar - Handshake Poller'
if (Get-ScheduledTask -TaskName $workerName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $workerName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $workerName -Confirm:$false
}
Write-Output 'Handshake poller removed. State, logs and credentials retained; scanner tasks unchanged.'
