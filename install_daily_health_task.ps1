param([string]$At = '09:00', [switch]$CurrentUser)
$ErrorActionPreference = 'Stop'
$healthRoot = $PSScriptRoot
$healthResult = Join-Path $healthRoot 'data\daily-health-task-install.json'
try {
    $healthIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $healthAdmin = [Security.Principal.WindowsPrincipal]::new($healthIdentity)
    if (-not $CurrentUser -and -not $healthAdmin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this installer as administrator.'
    }
    $healthDirectory = Join-Path $healthRoot 'data\provider-health'
    New-Item -ItemType Directory -Force -Path $healthDirectory | Out-Null
    $healthAction = New-ScheduledTaskAction -Execute (Join-Path $healthRoot '.venv\Scripts\python.exe') `
        -Argument "-B -m crypto_radar.daily_health --db `"$healthRoot\data\crypto_radar.db`" --output-dir `"$healthDirectory`"" -WorkingDirectory $healthRoot
    $healthTrigger = New-ScheduledTaskTrigger -Daily -At $At
    $healthSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $healthAccount = if ($CurrentUser) {
        New-ScheduledTaskPrincipal -UserId $healthIdentity.Name -LogonType Interactive -RunLevel Limited
    } else {
        New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount -RunLevel Limited
    }
    $healthName = 'Crypto Radar - Daily ApeWisdom Health'
    Register-ScheduledTask -TaskName $healthName -Action $healthAction -Trigger $healthTrigger `
        -Settings $healthSettings -Principal $healthAccount `
        -Description 'Read-only daily ApeWisdom health; stores local JSON and sends no messages.' -Force | Out-Null
    if (-not $CurrentUser) {
        $healthService = New-Object -ComObject 'Schedule.Service'
        $healthService.Connect()
        $healthTask = $healthService.GetFolder('\').GetTask($healthName)
        $healthOwner = $healthIdentity.User.Value
        $healthTask.SetSecurityDescriptor("D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;LS)(A;;GA;;;$healthOwner)",0)
    }
    Start-ScheduledTask -TaskName $healthName
    $healthInfo = Get-ScheduledTaskInfo -TaskName $healthName
    @{status='installed'; task=$healthName; at=$At; current_user=[bool]$CurrentUser; timezone=(Get-TimeZone).Id; next_run=$healthInfo.NextRunTime.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath $healthResult -Encoding utf8
} catch {
    @{status='failed'; error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $healthResult -Encoding utf8
    throw
}
