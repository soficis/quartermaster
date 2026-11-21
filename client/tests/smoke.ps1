<#
.SYNOPSIS
  End-to-end test of client/sync.ps1 against a local publish folder.

.DESCRIPTION
  Builds a throwaway publish folder and a fake WoW install, then drives the
  real script as a child process. Covers: -WhatIf writes nothing, install,
  "up to date", upgrade with backup, corrupt sha256 leaves the old addon in
  place, Setup.ps1 -NoSchedule stops after writing the config, and both
  scheduler installers honour -WhatIf and refuse bad times.

  No network, no real scheduler, no real game folder. Exits 1 when a check fails.

.EXAMPLE
  pwsh -NoProfile -File client/tests/smoke.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$client = Split-Path -Parent $here
$sync = Join-Path $client "sync.ps1"
$setup = Join-Path $client "Setup.ps1"
$feedSched = Join-Path (Split-Path -Parent $client) "feed/Install-FeedSchedule.ps1"
$hostExe = (Get-Process -Id $PID).Path

$script:failed = 0
function Check([bool]$Condition, [string]$Name) {
  if ($Condition) { Write-Host "ok   $Name" }
  else { Write-Host "FAIL $Name" -ForegroundColor Red; $script:failed++ }
}

$root = Join-Path ([IO.Path]::GetTempPath()) ("addonsync-smoke-" + [guid]::NewGuid().ToString("N"))
$publish = Join-Path $root "publish"
$wow = Join-Path $root "wow"
$addons = Join-Path $wow "_retail_/Interface/AddOns"
$backup = Join-Path $root "backup"
$logPath = Join-Path $root "sync.log"
$cfgPath = Join-Path $root "sync.config.json"
$src = Join-Path $root "src/DemoAddon"

function Write-Utf8([string]$Path, [string]$Text) {
  $enc = New-Object System.Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($Path, $Text, $enc)
}

function New-Publish([string]$Version, [string]$Sha = "", [bool]$Corrupt = $false,
  [string]$Flavor = "retail", [string]$WowDir = "") {
  Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $src | Out-Null
  Write-Utf8 (Join-Path $src "DemoAddon.toc") "## Interface: 110200`n## Version: $Version`n"
  Write-Utf8 (Join-Path $src "core.lua") "-- version $Version`n"

  $flavorDir = Join-Path $publish $Flavor
  New-Item -ItemType Directory -Force -Path $flavorDir | Out-Null
  $zipName = "DemoAddon-$Version.zip"
  $zip = Join-Path $flavorDir $zipName
  Compress-Archive -Path $src -DestinationPath $zip -Force

  $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
  if (!$Sha) { $Sha = $hash }
  if ($Corrupt) { $Sha = "0" * 64 }

  $entry = [ordered]@{
    addon   = "DemoAddon"
    version = $Version
    zip     = $zipName
    sha256  = $Sha
    size    = (Get-Item -LiteralPath $zip).Length
    source  = "smoke"
  }
  if ($WowDir) { $entry.wow_dir = $WowDir }
  if ($Sha -eq "omit") { $entry.Remove("sha256") }
  $manifest = [ordered]@{
    generated = [int](New-TimeSpan -Start ([datetime]"1970-01-01") -End (Get-Date)).TotalSeconds
    flavors   = @{ $Flavor = $entry }
  }
  Write-Utf8 (Join-Path $publish "manifest.json") ($manifest | ConvertTo-Json -Depth 6)
}

function Invoke-Sync([string[]]$Extra = @(), [string]$Cfg = $cfgPath) {
  # Under 5.1, child stderr captured with 2>&1 becomes an error record; with
  # "Stop" that would abort the whole suite instead of failing one check.
  $ErrorActionPreference = "Continue"
  $out = & $hostExe -NoProfile -ExecutionPolicy Bypass -File $sync `
    -Config $Cfg @Extra 2>&1
  [pscustomobject]@{ Code = $LASTEXITCODE; Output = (@($out) -join "`n") }
}

function Invoke-Sched([string[]]$Extra = @()) {
  # Same stderr handling as Invoke-Sync; -WhatIf keeps every real scheduler
  # (Task Scheduler, launchd, systemd) untouched.
  $ErrorActionPreference = "Continue"
  $out = & $hostExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $client "Install-Schedule.ps1") @Extra 2>&1
  [pscustomobject]@{ Code = $LASTEXITCODE; Output = (@($out) -join "`n") }
}

function Invoke-FeedSched([string[]]$Extra = @()) {
  $ErrorActionPreference = "Continue"
  $out = & $hostExe -NoProfile -ExecutionPolicy Bypass -File $feedSched @Extra 2>&1
  [pscustomobject]@{ Code = $LASTEXITCODE; Output = (@($out) -join "`n") }
}

function Read-Text([string]$Path) {
  # "" + : -Raw returns $null for an empty file, and checks call .Trim().
  if (Test-Path -LiteralPath $Path) { return "" + (Get-Content -LiteralPath $Path -Raw) }
  return ""
}

try {
  New-Item -ItemType Directory -Force -Path $addons | Out-Null
  New-Publish "11.2.0"

  $config = [ordered]@{
    ManifestBase     = $publish
    Roots            = @($wow)
    Include          = @("retail")
    BackupDir        = $backup
    LogPath          = $logPath
    ActiveWithinDays = 0
  }
  Write-Utf8 $cfgPath ($config | ConvertTo-Json -Depth 4)

  $r = Invoke-Sync @("-WhatIf")
  Check ($r.Code -eq 0) "whatif exits 0"
  Check ((Read-Text $logPath) -match "WHATIF: retail 11.2.0") "whatif logs the planned install"
  Check (!(Test-Path -LiteralPath (Join-Path $addons "DemoAddon"))) "whatif writes nothing"

  $r = Invoke-Sync
  $addonPath = Join-Path $addons "DemoAddon"
  $marker = Join-Path $addonPath ".synced-version"
  Check ($r.Code -eq 0) "install exits 0"
  Check ((Read-Text $marker).Trim() -eq "11.2.0") "marker records 11.2.0"
  Check (Test-Path -LiteralPath (Join-Path $addonPath "DemoAddon.toc")) "toc installed"
  Check ((Read-Text $logPath) -match "installed: retail 11.2.0") "install is logged"

  $r = Invoke-Sync
  Check ((Read-Text $logPath) -match "up to date \(11.2.0\)") "second run is a no-op"

  New-Publish "11.2.1"
  $r = Invoke-Sync
  Check ((Read-Text $marker).Trim() -eq "11.2.1") "upgrade updates the marker"
  $backups = @(Get-ChildItem -LiteralPath (Join-Path $backup "AddOns/retail") -Directory -ErrorAction SilentlyContinue)
  Check ($backups.Count -eq 1) "upgrade keeps one backup of the old addon"
  Check ((Read-Text (Join-Path $backups[0].FullName ".synced-version")).Trim() -eq "11.2.0") "backup holds the old version"

  New-Publish "11.2.2" -Corrupt $true
  $r = Invoke-Sync
  Check ((Read-Text $logPath) -match "sha256 mismatch") "bad hash is reported"
  Check ((Read-Text $marker).Trim() -eq "11.2.1") "bad hash leaves the working addon"
  Check ($r.Code -ne 0) "a failed install exits non-zero"

  # -WhatIf on a clean machine: the log folder does not exist yet.
  New-Publish "11.2.3"
  $backupDir = Join-Path $backup "AddOns/retail"
  $before = @(Get-ChildItem -LiteralPath $backupDir -Directory).Count
  $freshLog = Join-Path $root "fresh/logs/sync.log"
  $r = Invoke-Sync @("-WhatIf", "-LogPath", $freshLog)
  Check ($r.Code -eq 0) "whatif exits 0 when the log folder is missing"
  Check ((Read-Text $freshLog) -match "WHATIF: retail 11.2.3") "whatif creates and writes the log"
  Check (@(Get-ChildItem -LiteralPath $backupDir -Directory).Count -eq $before) "whatif keeps every backup"
  Check ((Read-Text $marker).Trim() -eq "11.2.1") "whatif leaves the addon alone"

  # A manifest entry without a sha256 is refused, not installed blind.
  New-Publish "11.2.4" -Sha "omit"
  $r = Invoke-Sync
  Check ($r.Code -ne 0) "missing sha256 exits non-zero"
  Check ((Read-Text $logPath) -match "invalid sha256") "missing sha256 is reported"
  Check ((Read-Text $marker).Trim() -eq "11.2.1") "missing sha256 installs nothing"

  # An empty marker is "unmanaged", not a crash of the whole run.
  Write-Utf8 $marker ""
  New-Publish "11.2.5"
  $r = Invoke-Sync
  Check ($r.Code -eq 0) "an empty marker does not end the run"
  Check ((Read-Text $marker).Trim() -eq "11.2.5") "an empty marker is replaced"

  # Hand-installed copy, no marker: a major.minor toc equal to the release
  # is taken over; a strictly newer one is left alone.
  New-Publish "11.2.0"
  Remove-Item -LiteralPath $addonPath -Recurse -Force
  New-Item -ItemType Directory -Force -Path $addonPath | Out-Null
  Write-Utf8 (Join-Path $addonPath "DemoAddon.toc") "## Version: 11.2`n"
  $r = Invoke-Sync
  Check ((Read-Text $marker).Trim() -eq "11.2.0") "toc 11.2 is taken over by 11.2.0"
  Remove-Item -LiteralPath $addonPath -Recurse -Force
  New-Item -ItemType Directory -Force -Path $addonPath | Out-Null
  Write-Utf8 (Join-Path $addonPath "DemoAddon.toc") "## Version: 12.0`n"
  $r = Invoke-Sync
  Check (!(Test-Path -LiteralPath $marker)) "a newer hand-installed copy is left alone"

  # Two installs of one flavor updated in the same run: separate backups.
  $wowB = Join-Path $root "wowB"
  $backup2 = Join-Path $root "backup2"
  foreach ($w in @($wow, $wowB)) {
    $a = Join-Path $w "_retail_/Interface/AddOns/DemoAddon"
    Remove-Item -LiteralPath $a -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $a | Out-Null
    Write-Utf8 (Join-Path $a ".synced-version") "1.0`n"
    Write-Utf8 (Join-Path $a "owner.txt") (Split-Path -Leaf $w)
  }
  $cfg2 = Join-Path $root "sync2.config.json"
  $c2 = [ordered]@{ ManifestBase = $publish; Roots = @($wow, $wowB); BackupDir = $backup2
    LogPath = $logPath; ActiveWithinDays = 0 }
  Write-Utf8 $cfg2 ($c2 | ConvertTo-Json -Depth 4)
  New-Publish "11.3.0"
  $r = Invoke-Sync -Cfg $cfg2
  $b2 = @(Get-ChildItem -LiteralPath (Join-Path $backup2 "AddOns/retail") -Directory -ErrorAction SilentlyContinue)
  $owners = @($b2 | ForEach-Object { (Read-Text (Join-Path $_.FullName "owner.txt")).Trim() } | Sort-Object)
  Check ($r.Code -eq 0 -and $b2.Count -eq 2) "two installs of one flavor get two backups"
  Check (($owners -join ",") -eq "wow,wowB") "each backup holds its own install"

  # A flavor outside the built-in five installs through its wow_dir.
  $wowP = Join-Path $root "wowP"
  New-Item -ItemType Directory -Force -Path (Join-Path $wowP "_ptr_/Interface/AddOns") | Out-Null
  $cfg3 = Join-Path $root "sync3.config.json"
  $c3 = [ordered]@{ ManifestBase = $publish; Roots = @($wowP); Include = @("ptr")
    BackupDir = $backup2; LogPath = $logPath }
  Write-Utf8 $cfg3 ($c3 | ConvertTo-Json -Depth 4)
  New-Publish "1.0.0" -Flavor "ptr" -WowDir "_ptr_"
  $r = Invoke-Sync -Cfg $cfg3
  Check ((Read-Text (Join-Path $wowP "_ptr_/Interface/AddOns/DemoAddon/.synced-version")).Trim() -eq "1.0.0") "an extra flavor installs via wow_dir"

  # Activity: only SavedVariables moved recently; the top of WTF is old.
  $wowL = Join-Path $root "wowL"
  $wtf = Join-Path $wowL "_retail_/WTF"
  $sv = Join-Path $wtf "Account/ACCT/Realm/Char/SavedVariables"
  New-Item -ItemType Directory -Force -Path (Join-Path $wowL "_retail_/Interface/AddOns"), $sv | Out-Null
  Write-Utf8 (Join-Path $wtf "Config.wtf") "SET x `"1`"`n"
  $old = (Get-Date).AddDays(-90)
  foreach ($p in @((Join-Path $wtf "Config.wtf"), (Join-Path $wtf "Account"), (Join-Path $wtf "Account/ACCT"),
      (Join-Path $wtf "Account/ACCT/Realm"), (Join-Path $wtf "Account/ACCT/Realm/Char"), $wtf)) {
    (Get-Item -LiteralPath $p).LastWriteTime = $old
  }
  $cfg4 = Join-Path $root "sync4.config.json"
  $c4 = [ordered]@{ ManifestBase = $publish; Roots = @($wowL); BackupDir = $backup2
    LogPath = $logPath; ActiveWithinDays = 30 }
  Write-Utf8 $cfg4 ($c4 | ConvertTo-Json -Depth 4)
  New-Publish "11.3.0"
  $r = Invoke-Sync -Cfg $cfg4
  Check (Test-Path -LiteralPath (Join-Path $wowL "_retail_/Interface/AddOns/DemoAddon/.synced-version")) "recent SavedVariables mark an install active"

  $setupCopy = Join-Path $root "client"
  New-Item -ItemType Directory -Force -Path $setupCopy | Out-Null
  Copy-Item -LiteralPath $setup -Destination $setupCopy
  $out = & $hostExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $setupCopy "Setup.ps1") `
    -ManifestBase $publish -Roots $wow -Yes -NoSchedule 2>&1
  $setupCfg = Join-Path $setupCopy "sync.config.json"
  Check ($LASTEXITCODE -eq 0) "setup -NoSchedule exits 0"
  Check (Test-Path -LiteralPath $setupCfg) "setup writes the config"
  Check ((@($out) -join "`n") -match "no scheduler registered") "setup stops before the scheduler"

  # Re-running Setup keeps the settings it does not ask about.
  $kept = Get-Content -LiteralPath $setupCfg -Raw | ConvertFrom-Json
  $kept | Add-Member -NotePropertyName Include -NotePropertyValue @("classic") -Force
  $kept | Add-Member -NotePropertyName BackupDir -NotePropertyValue "D:\Backups" -Force
  Write-Utf8 $setupCfg ($kept | ConvertTo-Json -Depth 4)
  $out = & $hostExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $setupCopy "Setup.ps1") `
    -ManifestBase $publish -Roots $wowB -Yes -NoSchedule 2>&1
  $again = Get-Content -LiteralPath $setupCfg -Raw | ConvertFrom-Json
  Check ((@($again.Include) -join ",") -eq "classic" -and $again.BackupDir -eq "D:\Backups") "setup rerun keeps Include and BackupDir"
  Check ((@($again.Roots) -join ",") -eq $wowB) "setup rerun updates Roots"

  # -Roots a,b from -File (bash/cmd) arrives as one string; it must not
  # become a single bogus root.
  $out = & $hostExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $setupCopy "Setup.ps1") `
    -ManifestBase $publish -Roots "$wow,$wowB" -Yes -NoSchedule 2>&1
  $both = Get-Content -LiteralPath $setupCfg -Raw | ConvertFrom-Json
  Check (@($both.Roots).Count -eq 2) "a comma-separated -Roots is split"

  # Install-Schedule: "-Times 07:30,19:30" is one string when the script is run
  # with -File from bash or cmd, and used to become an unschedulable unit that
  # was reported as enabled. -WhatIf keeps the real scheduler untouched.
  $s = Invoke-Sched @("-Times", "07:30,19:30", "-TaskName", "AddonSyncSmoke", "-WhatIf")
  Check ($s.Code -eq 0) "a comma-separated -Times is accepted"
  Check ($s.Output -match "What if") "install-schedule honours -WhatIf"
  $leaked = if (!$IsLinux -and !$IsMacOS) {
    $null -ne (Get-ScheduledTask -TaskName "AddonSyncSmoke" -ErrorAction SilentlyContinue)
  } else {
    Test-Path -LiteralPath (Join-Path $HOME ".config/systemd/user/AddonSyncSmoke.timer")
  }
  Check (!$leaked) "install-schedule -WhatIf registers nothing"
  $s = Invoke-Sched @("-Times", "nonsense", "-TaskName", "AddonSyncSmoke", "-WhatIf")
  Check ($s.Code -ne 0 -and $s.Output -match "bad -Times") "a bad -Times value is refused"
  $s = Invoke-Sched @("-TaskName", "AddonSyncSmoke", "-Uninstall", "-WhatIf")
  Check ($s.Code -eq 0 -and $s.Output -match "What if" -and
    $s.Output -notmatch "removed\.") "install-schedule -Uninstall honours -WhatIf"

  # The feed scheduler is the same kind of script; dry-run it here so a
  # macOS/Windows/Linux branch cannot regress unnoticed.
  $s = Invoke-FeedSched @("-At", "05:30", "-TaskName", "AddonFeedSmoke", "-WhatIf")
  Check ($s.Code -eq 0) "feed scheduler accepts a normal -At"
  Check ($s.Output -match "What if") "feed scheduler honours -WhatIf"
  $feedLeak = if (!$IsLinux -and !$IsMacOS) {
    $null -ne (Get-ScheduledTask -TaskName "AddonFeedSmoke" -ErrorAction SilentlyContinue)
  } elseif ($IsMacOS) {
    Test-Path -LiteralPath (Join-Path $HOME "Library/LaunchAgents/com.addonsync.feed.plist")
  } else {
    Test-Path -LiteralPath (Join-Path $HOME ".config/systemd/user/AddonFeedSmoke.timer")
  }
  Check (!$feedLeak) "feed scheduler -WhatIf registers nothing"
  $s = Invoke-FeedSched @("-At", "nonsense", "-TaskName", "AddonFeedSmoke", "-WhatIf")
  Check ($s.Code -ne 0 -and $s.Output -match "bad -At") "a bad -At value is refused"
}
finally {
  Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

if ($script:failed) {
  Write-Host "$($script:failed) check(s) failed" -ForegroundColor Red
  exit 1
}
Write-Host "all client checks passed"
exit 0
