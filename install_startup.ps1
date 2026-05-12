# install_startup.ps1
# -------------------
# Registers FireRiskMap.exe with Windows Task Scheduler so it:
#   - Starts automatically at every user logon
#   - Runs silently in the background (the exe handles the 13:30 daily trigger internally)
#
# Run once as Administrator after building the exe with build.bat.

$exePath = Join-Path $PSScriptRoot "dist\FireRiskMap.exe"

if (-not (Test-Path $exePath)) {
    Write-Error "Executable not found at '$exePath'. Run build.bat first."
    exit 1
}

$taskName = "FireRiskMap"

# Remove any existing task with the same name to ensure a clean install.
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $exePath

# Trigger: start the process each time the current user logs on.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit     0 `
    -RestartCount           3 `
    -RestartInterval        (New-TimeSpan -Minutes 1) `
    -MultipleInstances      IgnoreNew `
    -StartWhenAvailable     $true

Register-ScheduledTask `
    -TaskName   $taskName `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -RunLevel   Highest `
    -Force | Out-Null

Write-Host ""
Write-Host "Done. Task '$taskName' registered successfully."
Write-Host "FireRiskMap.exe will start at logon and run the pipeline daily at 13:30."
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Start now :  Start-ScheduledTask  -TaskName '$taskName'"
Write-Host "  Stop      :  Stop-ScheduledTask   -TaskName '$taskName'"
Write-Host "  Remove    :  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
