param([string]$At = '09:00', [switch]$Drive)
$ErrorActionPreference = 'Stop'
$reportRoot = $PSScriptRoot
$reportIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$reportPrincipal = [Security.Principal.WindowsPrincipal]::new($reportIdentity)
if (-not $reportPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run as administrator to install the weekly report task.'
}
$reportArguments = '-B -m crypto_radar.weekly_report'
if ($Drive) { $reportArguments += ' --drive' }
$reportAction = New-ScheduledTaskAction -Execute (Join-Path $reportRoot '.venv\Scripts\python.exe') `
    -Argument $reportArguments -WorkingDirectory $reportRoot
$reportTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday -At $At
$reportSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$reportAccount = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount -RunLevel Limited
$reportName = 'Crypto Radar - Weekly Files'
Register-ScheduledTask -TaskName $reportName -Action $reportAction -Trigger $reportTrigger `
    -Settings $reportSettings -Principal $reportAccount -Description 'Deliver a consistent database snapshot, config.yaml and statistical.py every Monday.' -Force | Out-Null
$reportService = New-Object -ComObject 'Schedule.Service'
$reportService.Connect()
$reportTask = $reportService.GetFolder('\').GetTask($reportName)
$reportSid = $reportIdentity.User.Value
$reportTask.SetSecurityDescriptor("D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;LS)(A;;GA;;;$reportSid)",0)
# Reload attachment support; scanner remains running.
if (-not $Drive) {
    Stop-ScheduledTask -TaskName 'Crypto Radar - Mail Relay'
    Start-ScheduledTask -TaskName 'Crypto Radar - Mail Relay'
}
$reportTaskInfo = Get-ScheduledTaskInfo -TaskName $reportName
@{name=$reportName; time=$At; day='Monday'; next_run=$reportTaskInfo.NextRunTime.ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportRoot 'data\weekly-task-install.json') -Encoding utf8
