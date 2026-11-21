<#
.SYNOPSIS
  Register sync.ps1 on the platform scheduler (Windows/macOS/Linux).

.DESCRIPTION
  Windows: registers a Scheduled Task (runs as you, so mapped drives and
  your WoW folders are visible).
  macOS: installs a per-user launchd agent (~/Library/LaunchAgents).
  Linux: installs a per-user systemd timer (systemctl --user).

  Re-running replaces the schedule in place. -Uninstall removes it.

.EXAMPLE
  .\Install-Schedule.ps1
  .\Install-Schedule.ps1 -Times 06:30,18:30
  .\Install-Schedule.ps1 -Uninstall
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string[]]$Times = @("07:30", "19:30"),
  [string]$TaskName = "AddonSync",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "sync.ps1"
if (!(Test-Path -LiteralPath $script)) { throw "missing $script" }
if ($Times.Count -eq 0) { throw "no times given" }

$isWin = !$IsLinux -and !$IsMacOS
$pwsh = if ($isWin) { "powershell.exe" } else {
  (Get-Command pwsh -ErrorAction SilentlyContinue | Select-Object -First 1).Source
}
if (!$pwsh) { throw "pwsh not found - install PowerShell 7 first (https://aka.ms/ps7)" }

function Quote([string]$s) { return '"{0}"' -f ($s -replace '"', '""') }

if ($Uninstall) {
  if ($isWin) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  } elseif ($IsMacOS) {
    $plist = Join-Path $HOME "Library/LaunchAgents/com.addonsync.plist"
    & launchctl unload $plist 2>$null
    Remove-Item -LiteralPath $plist -Force -ErrorAction SilentlyContinue
  } else {
    & systemctl --user disable --now "$TaskName.timer" 2>$null
    Remove-Item -LiteralPath (Join-Path $HOME ".config/systemd/user/$TaskName.service") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $HOME ".config/systemd/user/$TaskName.timer") -Force -ErrorAction SilentlyContinue
    & systemctl --user daemon-reload 2>$null
  }
  Write-Host "schedule '$TaskName' removed."
  return
}

if ($isWin) {
  $arg = '-NoProfile -ExecutionPolicy Bypass -File {0}' -f (Quote $script)
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
  $triggers = @()
  foreach ($t in $Times) { $triggers += New-ScheduledTaskTrigger -Daily -At $t }
  $settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
  if ($PSCmdlet.ShouldProcess($TaskName, "register scheduled task")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
      -Settings $settings -Description "Install published addon builds into active WoW installs" `
      -Force | Out-Null
    Write-Host "Registered task '$TaskName' at $($Times -join ', ')."
    Write-Host "Run it now:  Start-ScheduledTask -TaskName $TaskName"
    Write-Host "Check it:    Get-ScheduledTaskInfo -TaskName $TaskName"
  }
  return
}

$args = @("-NoProfile", "-File", $script)
if ($IsMacOS) {
  $dir = Join-Path $HOME "Library/LaunchAgents"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $entries = foreach ($t in $Times) {
    $h, $m = $t -split ":"
    "      <dict><key>Hour</key><integer>$([int]$h)</integer><key>Minute</key><integer>$([int]$m)</integer></dict>"
  }
  $plist = Join-Path $dir "com.addonsync.plist"
  @"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.addonsync</string>
  <key>ProgramArguments</key>
  <array>
    <string>$pwsh</string>
    <string>-NoProfile</string>
    <string>-File</string>
    <string>$script</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
$($entries -join "`n")
  </array>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/addonsync.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/addonsync.log</string>
</dict>
</plist>
"@ | Set-Content -LiteralPath $plist -Encoding UTF8
  & launchctl unload $plist 2>$null
  & launchctl load $plist
  Write-Host "Loaded launchd agent com.addonsync at $($Times -join ', ')."
  Write-Host "Run it now:  launchctl start com.addonsync"
  return
}

# Linux: systemd user units.
$dir = Join-Path $HOME ".config/systemd/user"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$oncals = foreach ($t in $Times) { "OnCalendar=*-*-* $t`:00" }
@"
[Unit]
Description=Install published addon builds into active WoW installs

[Service]
Type=oneshot
ExecStart=$pwsh -NoProfile -File "$script"
Nice=10
"@ | Set-Content -LiteralPath (Join-Path $dir "$TaskName.service") -Encoding UTF8 -NoNewline
@"
[Unit]
Description=Run the addon sync daily

[Timer]
$($oncals -join "`n")
Persistent=true
Unit=$TaskName.service

[Install]
WantedBy=timers.target
"@ | Set-Content -LiteralPath (Join-Path $dir "$TaskName.timer") -Encoding UTF8 -NoNewline
& systemctl --user daemon-reload
& systemctl --user enable --now "$TaskName.timer"
Write-Host "Enabled user timer '$TaskName' at $($Times -join ', ')."
Write-Host "Check it:    systemctl --user list-timers $TaskName.timer"
Write-Host "Note: user timers only fire while you are logged in, unless lingering is on (loginctl enable-linger $env:USER)."
