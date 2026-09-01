param(
    [string]$TaskName = 'NOFX Discord Auto Checkin',
    [switch]$Enable
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$uv = (Get-Command uv).Source
$argument = "-NoProfile -WindowStyle Hidden -Command `"Set-Location -LiteralPath '$repo'; & '$uv' run python -u nofx_discord.py run-day`""
$action = New-ScheduledTaskAction -Execute 'pwsh.exe' -Argument $argument
$trigger = New-ScheduledTaskTrigger -Daily -At '00:00'
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

if (-not $Enable) {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
}

$task = Get-ScheduledTask -TaskName $TaskName
Write-Output "TASK_NAME=$TaskName"
Write-Output "TASK_STATE=$($task.State)"
Write-Output "TASK_ENABLED=$($task.Settings.Enabled)"
