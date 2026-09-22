# Run once as administrator. No passwords are passed to Task Scheduler.
param([ValidateSet('v11','v12','all')][string]$Detector = 'v11')
$ErrorActionPreference = "Stop"
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskData = Join-Path $taskRoot 'data'
$taskLogs = Join-Path $taskRoot 'logs'
$taskResult = Join-Path $taskData 'startup-install-result.json'
$taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$taskAdmin = [Security.Principal.WindowsPrincipal]::new($taskIdentity)
if (-not $taskAdmin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script as administrator to install boot tasks.'
}
try {
    New-Item -ItemType Directory -Force -Path $taskData, $taskLogs | Out-Null
    $taskRuntime = (& $taskPython -B -c 'import sys; print(sys.base_prefix)').Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $taskRuntime)) {
        throw 'Cannot locate the Python runtime.'
    }
    # Local Service: read code/runtime/settings; write only data and logs.
    foreach ($taskReadPath in @($taskRoot, $taskRuntime)) {
        & icacls.exe $taskReadPath /grant '*S-1-5-19:(OI)(CI)RX' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Cannot grant runtime access: $taskReadPath" }
    }
    foreach ($taskWritePath in @($taskData, $taskLogs)) {
        & icacls.exe $taskWritePath /grant '*S-1-5-19:(OI)(CI)M' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Cannot grant data access: $taskWritePath" }
    }
    $taskAccount = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount -RunLevel Limited
    $taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $taskDefinitions = @(
        @{ Name = 'Crypto Radar - Mail Relay'; Module = 'crypto_radar.mail_relay'; Delay = 'PT30S' },
        @{ Name = 'Crypto Radar - Scanner'; Module = 'crypto_radar.main'; Delay = 'PT90S' }
    )
    foreach ($taskDefinition in $taskDefinitions) {
        $taskTrigger = New-ScheduledTaskTrigger -AtStartup
        $taskTrigger.Delay = $taskDefinition.Delay
        $taskArguments = "-B -m $($taskDefinition.Module)"
        if ($taskDefinition.Module -eq 'crypto_radar.main') { $taskArguments += " --detector $Detector" }
        $taskAction = New-ScheduledTaskAction -Execute $taskPython `
            -Argument $taskArguments -WorkingDirectory $taskRoot
        Register-ScheduledTask -TaskName $taskDefinition.Name -Action $taskAction `
            -Trigger $taskTrigger -Settings $taskSettings -Principal $taskAccount `
            -Description 'Crypto Radar automatic boot startup, with restart on failure.' -Force | Out-Null
        $taskService = New-Object -ComObject 'Schedule.Service'
        $taskService.Connect()
        $taskRegistered = $taskService.GetFolder('\').GetTask($taskDefinition.Name)
        $taskOwnerSid = $taskIdentity.User.Value
        $taskRegistered.SetSecurityDescriptor("D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;LS)(A;;GA;;;$taskOwnerSid)", 0)
    }
    # Hand over existing manually started instances to Task Scheduler.
    foreach ($taskDefinition in $taskDefinitions) {
        Stop-ScheduledTask -TaskName $taskDefinition.Name -ErrorAction SilentlyContinue
    }
    $taskProcesses = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and
        $_.CommandLine -like "*$taskPython*" -and
        $_.CommandLine -match ' -m crypto_radar\.(mail_relay|main)(\s|$)'
    }
    foreach ($taskProcess in $taskProcesses) {
        Stop-Process -Id $taskProcess.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-ScheduledTask -TaskName 'Crypto Radar - Mail Relay'
    Start-Sleep -Seconds 5
    # No mail is sent: check that the relay accepts a TCP connection.
    $taskProbe = [Net.Sockets.TcpClient]::new()
    try { $taskProbe.Connect('127.0.0.1', 1025) } finally { $taskProbe.Dispose() }
    Start-ScheduledTask -TaskName 'Crypto Radar - Scanner'
    @{ status = 'installed'; timestamp = [DateTime]::UtcNow.ToString('o'); account = 'LOCAL SERVICE' } |
        ConvertTo-Json | Set-Content -LiteralPath $taskResult -Encoding utf8
} catch {
    @{ status = 'failed'; timestamp = [DateTime]::UtcNow.ToString('o'); error = $_.Exception.Message } |
        ConvertTo-Json | Set-Content -LiteralPath $taskResult -Encoding utf8
    throw
}
