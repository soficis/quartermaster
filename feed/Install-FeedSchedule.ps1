<#
.SYNOPSIS
  Register the feed worker on the Windows Task Scheduler.

.DESCRIPTION
  Windows counterpart of feed/addonsync-feed.timer: one check a day, at a
  random time inside a window, running as you so mapped drives, the browser
  profile and the publish folder are all reachable. The worker logs to
  state\worker.log; pythonw.exe is used when it exists, so no console flashes.

  Re-running replaces the task. -Uninstall removes it.

.EXAMPLE
  .\Install-FeedSchedule.ps1
  .\Install-FeedSchedule.ps1 -At 05:00 -RandomMinutes 120
  .\Install-FeedSchedule.ps1 -TaskName AddonFeed -Uninstall
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$TaskName = "AddonFeed",
  [string]$At = "05:30",
  [int]$RandomMinutes = 180,
  [string]$Python = "",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here
$worker = Join-Path $here "worker.py"
if (!(Test-Path -LiteralPath $worker)) { throw "missing $worker" }

if ($Uninstall) {
  if ($PSCmdlet.ShouldProcess($TaskName, "unregister scheduled task")) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false `
      -ErrorAction SilentlyContinue
    Write-Host "task '$TaskName' removed."
  }
  return
}

if (!$Python) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if (!$cmd) { throw "python is not on PATH; install it or pass -Python <exe>" }
  $Python = $cmd.Source
}
if (!(Test-Path -LiteralPath $Python)) { throw "python not found: $Python" }
$pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
if (Test-Path -LiteralPath $pythonw) { $Python = $pythonw }

if ($RandomMinutes -lt 0) { $RandomMinutes = 0 }

$action = New-ScheduledTaskAction -Execute $Python `
  -Argument ('"{0}" check --scheduled' -f $worker) `
  -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $At
if ($RandomMinutes -gt 0) {
  $trigger.RandomDelay = "PT{0}M" -f $RandomMinutes
}
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries

if ($PSCmdlet.ShouldProcess($TaskName, "register daily feed check at $At (+$RandomMinutes min)")) {
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Check the addon feed, download and publish" `
    -Force | Out-Null
  Write-Host "Registered task '$TaskName': daily at $At plus up to $RandomMinutes minutes."
  Write-Host "Run it now:  Start-ScheduledTask -TaskName $TaskName"
  Write-Host "Check it:    Get-ScheduledTaskInfo -TaskName $TaskName"
  Write-Host "Log:         $(Join-Path $here 'state\worker.log')"
}
