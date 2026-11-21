<#
.SYNOPSIS
  Register the feed worker on the platform scheduler (Windows/macOS/Linux).

.DESCRIPTION
  Windows: registers a Scheduled Task that runs as you, so mapped drives, the
  browser profile and the publish folder are all reachable.
  macOS: installs a per-user launchd agent (~/Library/LaunchAgents).
  Linux: installs a per-user systemd timer (systemctl --user), which is the
  non-root alternative to feed/install.sh.

  The worker runs `check --scheduled`, so the schedule itself decides when a
  check happens. Re-running replaces the schedule in place. -Uninstall
  removes it; -WhatIf shows what would happen and changes nothing.

  Windows randomises every run with Task Scheduler's RandomDelay. systemd
  randomises every run with RandomizedDelaySec. launchd has no per-run random
  delay, so on macOS the installer picks one random minute offset and keeps
  it: different Macs still spread out, this one runs at a fixed time.

.EXAMPLE
  .\Install-FeedSchedule.ps1
  .\Install-FeedSchedule.ps1 -At 06:00 -RandomMinutes 0
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

# "-At 5:30" from a shell that does not normalise it, or a value that could
# never be scheduled: refuse it before touching any scheduler.
if ($At -notmatch '^(?:[01]?\d|2[0-3]):[0-5]\d$') {
  throw "bad -At value '$At' (want HH:mm, like 05:30)"
}
$atHour, $atMin = $At -split ':'
$atHour = [int]$atHour
$atMin = [int]$atMin
$At = "{0:00}:{1:00}" -f $atHour, $atMin
if ($RandomMinutes -lt 0) { $RandomMinutes = 0 }

$isWin = !$IsLinux -and !$IsMacOS
$macLabel = "com.addonsync.feed"

if ($Uninstall) {
  if (!$PSCmdlet.ShouldProcess($TaskName, "remove the feed schedule")) { return }
  if ($isWin) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false `
      -ErrorAction SilentlyContinue
  } elseif ($IsMacOS) {
    $plist = Join-Path $HOME "Library/LaunchAgents/$macLabel.plist"
    & launchctl unload $plist 2>$null
    Remove-Item -LiteralPath $plist -Force -ErrorAction SilentlyContinue
  } else {
    & systemctl --user disable --now "$TaskName.timer" 2>$null
    Remove-Item -LiteralPath (Join-Path $HOME ".config/systemd/user/$TaskName.service") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $HOME ".config/systemd/user/$TaskName.timer") -Force -ErrorAction SilentlyContinue
    & systemctl --user daemon-reload 2>$null
  }
  Write-Host "feed schedule '$TaskName' removed."
  return
}

if (!$Python) {
  if ($isWin) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if (!$cmd) { throw "python is not on PATH; install it or pass -Python <exe>" }
  } else {
    $cmd = Get-Command python3 -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if (!$cmd) {
      $cmd = Get-Command python -ErrorAction SilentlyContinue |
        Select-Object -First 1
    }
    if (!$cmd) {
      throw "python3 is not on PATH; install Python 3 or pass -Python <exe>"
    }
  }
  $Python = $cmd.Source
}
if (!(Test-Path -LiteralPath $Python)) { throw "python not found: $Python" }
# The Microsoft Store alias on Windows and the Command Line Tools stub on
# macOS both pass Test-Path but do not run Python; a task registered against
# either would fail every day with no worker log.
& $Python -c "import sys" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw ("python at $Python does not run (Microsoft Store alias, or macOS " +
    "Command Line Tools not installed?); install Python 3 or pass -Python <exe>")
}
if ($isWin) {
  $pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
  if (Test-Path -LiteralPath $pythonw) { $Python = $pythonw }
}

if ($isWin) {
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
  return
}

if ($IsMacOS) {
  if (!$PSCmdlet.ShouldProcess($TaskName, "install launchd agent $macLabel")) { return }
  $offset = if ($RandomMinutes -gt 0) {
    Get-Random -Minimum 0 -Maximum ($RandomMinutes + 1)
  } else { 0 }
  $total = ($atHour * 60 + $atMin + $offset) % 1440
  $runHour = [int][math]::Floor($total / 60)
  $runMin = $total % 60
  # launchd starts jobs with a minimal PATH, so a brew-installed unrar/7z
  # would be invisible even though it works in your shell.
  $pathParts = @("/opt/homebrew/bin", "/usr/local/bin") +
    @($env:PATH -split ':' | Where-Object { $_ })
  $envPath = ($pathParts | Select-Object -Unique) -join ':'
  $dir = Join-Path $HOME "Library/LaunchAgents"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $plist = Join-Path $dir "$macLabel.plist"
  @"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$macLabel</string>
  <key>ProgramArguments</key>
  <array>
    <string>$Python</string>
    <string>$worker</string>
    <string>check</string>
    <string>--scheduled</string>
  </array>
  <key>WorkingDirectory</key><string>$repo</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$runHour</integer>
    <key>Minute</key><integer>$runMin</integer>
  </dict>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$envPath</string></dict>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/addonsync-feed.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/addonsync-feed.log</string>
</dict>
</plist>
"@ | Set-Content -LiteralPath $plist -Encoding UTF8
  & launchctl unload $plist 2>$null
  & launchctl load $plist
  if ($LASTEXITCODE -ne 0) {
    throw "launchctl could not load $plist (exit $LASTEXITCODE)"
  }
  Write-Host ("Loaded launchd agent {0}: daily at {1:00}:{2:00} (a fixed +{3} min offset picked now)." -f $macLabel, $runHour, $runMin, $offset)
  Write-Host "Run it now:  launchctl start $macLabel"
  Write-Host "Log:         $HOME/Library/Logs/addonsync-feed.log"
  return
}

# Linux: systemd user units. This is the no-root alternative to install.sh;
# use one or the other, not both.
if (!$PSCmdlet.ShouldProcess($TaskName, "install systemd user timer")) { return }
$dir = Join-Path $HOME ".config/systemd/user"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$delay = if ($RandomMinutes -gt 0) { "RandomizedDelaySec={0}m" -f $RandomMinutes } else { "" }
$envLine = 'Environment="PATH={0}"' -f ($env:PATH -replace '"', '')
@"
[Unit]
Description=Check the addon feed, download and publish

[Service]
Type=oneshot
ExecStart="$Python" "$worker" check --scheduled
WorkingDirectory=$repo
$envLine
Nice=10
"@ | Set-Content -LiteralPath (Join-Path $dir "$TaskName.service") -Encoding UTF8 -NoNewline
@"
[Unit]
Description=Check the addon feed daily

[Timer]
OnCalendar=*-*-* $At`:00
$delay
Persistent=true
Unit=$TaskName.service

[Install]
WantedBy=timers.target
"@ | Set-Content -LiteralPath (Join-Path $dir "$TaskName.timer") -Encoding UTF8 -NoNewline
& systemctl --user daemon-reload
& systemctl --user enable --now "$TaskName.timer"
# enable --now still exits 0 when it creates the symlink but cannot start the
# unit, so ask systemd for the real state before reporting success.
$state = & systemctl --user is-active "$TaskName.timer"
if ($LASTEXITCODE -ne 0) {
  throw "systemd did not start '$TaskName.timer' (state: $state). " +
    "Check: systemctl --user status $TaskName.timer"
}
Write-Host "Enabled user timer '$TaskName': daily at $At plus up to $RandomMinutes minutes."
Write-Host "Check it:    systemctl --user list-timers $TaskName.timer"
Write-Host "Note: user timers only fire while you are logged in, unless lingering is on (loginctl enable-linger $env:USER)."
