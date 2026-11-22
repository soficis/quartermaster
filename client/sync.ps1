<#
.SYNOPSIS
  Pull published addon builds into active local WoW installs.

.DESCRIPTION
  Reads <manifest>/manifest.json, compares each flavor's published version
  with the version marker in the installed addon, downloads the zip, verifies
  its SHA-256, backs up the old folder, and replaces it.

  <manifest> is any publish folder: a local path or an http(s) URL.
  Serve the feed worker's publish directory with a file share or static
  HTTP and point this at it.

  Only installs that look played are touched: a target counts as active when
  its WTF data was written within -ActiveWithinDays, or when it already
  carries a .synced-version marker from a previous sync. Anything else is
  skipped (logged), unless forced with -Include. Fresh installs that have
  never launched have no WTF data yet, so add them with -Include once.

  Settings come from sync.config.json next to this script (copy
  sync.config.example.json to start), or from the parameters below,
  which override the file. At minimum ManifestBase and one root must be
  set - run Setup.ps1 once to generate the config interactively.

  Flavors map to client folders:
    retail      -> _retail_
    forever     -> _forever_
    anniversary -> _anniversary_
    classic     -> _classic_   (+ any direct-client root, e.g. a 3.3.5 client)
    era         -> _classic_era_

  A root is a parent with those folders, or one direct client. A direct root
  uses -DirectFlavor (classic by default) unless the root folder itself is
  named for a flavor. Roots are expanded ("~", %VARS%), duplicates removed,
  and -Include/-Exclude accept either a flavor or a root path.

.PARAMETER ActiveWithinDays
  WTF data newer than this many days marks the install active. 0 disables
  the activity check (every found install is targeted).

.PARAMETER Include
  Always sync these, e.g. -Include classic,era or a root path. Bypasses
  the activity check.

.PARAMETER Exclude
  Never sync these (flavor names or root paths).

.EXAMPLE
  .\Setup.ps1              # first run: writes sync.config.json
  .\sync.ps1 -WhatIf
  .\sync.ps1 -Include classic -ActiveWithinDays 14
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [Alias("PiBase")]
  [string]$ManifestBase = "",
  [string[]]$Roots = @(),
  [string]$DirectFlavor = "",
  [string]$BackupDir = "",
  [string]$LogPath = "",
  [int]$ActiveWithinDays = -1,
  [string[]]$Include = @(),
  [string[]]$Exclude = @(),
  [string]$Config = "",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 redraws a progress bar per chunk, which makes
# Invoke-WebRequest many times slower on a multi-MB zip.
$ProgressPreference = "SilentlyContinue"
$here =Split-Path -Parent $MyInvocation.MyCommand.Path
$onWindows = !$IsLinux -and !$IsMacOS
$homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$TempDir = if ($env:TEMP) { $env:TEMP } else { [IO.Path]::GetTempPath() }

# Defaults per OS; the config file and explicit parameters override these.
$appData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA }
  elseif ($IsMacOS) { Join-Path $HOME "Library/Application Support" }
  elseif ($env:XDG_DATA_HOME) { $env:XDG_DATA_HOME }
  else { Join-Path $HOME ".local/share" }
$defaultRoots = if ($onWindows) { @("C:\Program Files (x86)\World of Warcraft") }
  elseif ($IsMacOS) { @("/Applications/World of Warcraft") }
  else { @() }
$defaults = @{
  ManifestBase = ""
  Roots = $defaultRoots
  DirectFlavor = "classic"
  BackupDir = (Join-Path $homeDir "WoWAddonBackups")
  LogPath = (Join-Path $appData "AddonSync/sync.log")
  ActiveWithinDays = 30
  Include = @()
  Exclude = @()
}

# Fill any unset parameter from sync.config.json (gitignored).
$cfgPath = if ($Config) { $Config } else { Join-Path $here "sync.config.json" }
# -WhatIf sets $WhatIfPreference, and Set-Variable honors it. Config
# resolution must still run, or -WhatIf reports a bogus missing ManifestBase.
$whatIfWas = $WhatIfPreference
$WhatIfPreference = $false
if (Test-Path -LiteralPath $cfgPath) {
  # -Encoding UTF8 reads BOM and BOM-less UTF-8 alike; 5.1's default (ANSI)
  # garbles a non-ASCII root saved by Notepad, and the root is then "missing".
  $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
  foreach ($name in $defaults.Keys) {
    if ($PSBoundParameters.ContainsKey($name)) { continue }
    if ($name -eq "ActiveWithinDays" -and $ActiveWithinDays -ge 0) { continue }
    $v = $cfg.$name
    if ($null -ne $v) { Set-Variable -Name $name -Value $v }
  }
}
foreach ($name in $defaults.Keys) {
  $cur = Get-Variable -Name $name -ValueOnly
  $empty = ($null -eq $cur) -or (($cur -is [array]) -and ($cur.Count -eq 0)) -or (($cur -is [string]) -and ($cur -eq "")) -or (($cur -is [int]) -and ($cur -lt 0))
  if ($empty) { Set-Variable -Name $name -Value $defaults[$name] }
}
$WhatIfPreference = $whatIfWas
if (!$ManifestBase) {
  throw "ManifestBase is not set. Copy client/sync.config.example.json to client/sync.config.json (or run client/Setup.ps1), or pass -ManifestBase explicitly."
}
$ManifestBase = $ManifestBase.TrimEnd('/', '\')
$FlavorOrder = @("forever", "retail", "anniversary", "classic", "era")
$FlavorDirs = @{
  "_retail_"      = "retail"
  "_forever_"     = "forever"
  "_anniversary_" = "anniversary"
  "_classic_"     = "classic"
  "_classic_era_" = "era"
}

function Get-ResolvedPath([string]$Path) {
  # Config files often carry "~/" on Unix-like systems or %VAR% on Windows.
  # Expand them so a root typed by a user matches the path we log and the
  # path -Include/-Exclude are compared against. Flavor names pass through
  # unchanged; callers only use this for paths.
  $s = "$Path".Trim()
  if ($s -match '^~([\\/]|$)') {
    $s = Join-Path $homeDir $s.Substring(1).TrimStart('\', '/')
  }
  if ($s -match '%') { $s = [Environment]::ExpandEnvironmentVariables($s) }
  if (!$s) { return $s }
  try { return [IO.Path]::GetFullPath($s) } catch { return $s }
}

# Canonical roots: no trailing separators, "~" and %VARS% expanded, duplicates
# removed. Otherwise one install can be targeted twice, and Include/Exclude by
# root path silently miss the target.
$Roots = @($Roots | ForEach-Object { Get-ResolvedPath $_ } | Where-Object { $_ } |
  Select-Object -Unique)

function Test-ListMatch([string[]]$List, $Target) {
  if ($List -contains $Target.Flavor) { return $true }
  foreach ($item in $List) {
    if ((Get-ResolvedPath $item) -eq $Target.Root) { return $true }
  }
  return $false
}

function Write-Log([string]$Message) {
  # Local shadow, before anything touches disk: -WhatIf must still produce a
  # log to review afterwards, and on a clean machine that means creating the
  # log folder too.
  $WhatIfPreference = $false
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  $dir = Split-Path -Parent $LogPath
  if ($dir -and !(Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
  Write-Host $line
}

function ConvertTo-Version([string]$Text) {
  if (!$Text) { return @() }
  # [long], not [int]: a date-stamped part (2026112101) overflows Int32 and
  # would throw outside any try; Python's version_tuple has no limit.
  return @([regex]::Matches($Text, "\d+") | ForEach-Object { [long]$_.Value })
}

function Test-Newer([string]$Candidate, [string]$Current) {
  if (!$Current) { return $true }
  $a = ConvertTo-Version $Candidate
  $b = ConvertTo-Version $Current
  $n = [Math]::Max($a.Count, $b.Count)
  for ($i = 0; $i -lt $n; $i++) {
    $x = if ($i -lt $a.Count) { $a[$i] } else { 0 }
    $y = if ($i -lt $b.Count) { $b[$i] } else { 0 }
    if ($x -gt $y) { return $true }
    if ($x -lt $y) { return $false }
  }
  return $false
}

function Get-MarkerVersion([string]$AddonPath) {
  # The version a previous sync installed; "" when unmanaged. An empty or
  # unreadable marker counts as unmanaged instead of ending the whole run.
  $marker = Join-Path $AddonPath ".synced-version"
  if (Test-Path -LiteralPath $marker) {
    return ("" + (Get-Content -LiteralPath $marker -TotalCount 1 -ErrorAction SilentlyContinue)).Trim()
  }
  return ""
}

function Get-TocVersion([string]$AddonPath) {
  $toc = Get-ChildItem -LiteralPath $AddonPath -Filter "*.toc" -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($toc) {
    $line = Select-String -LiteralPath $toc.FullName -Pattern '^##\s*Version:\s*(.+)$' |
      Select-Object -First 1
    if ($line) { return $line.Matches[0].Groups[1].Value.Trim() }
  }
  return ""
}

function Get-WowTargets {
  $out = @()
  foreach ($root in $Roots) {
    if (!(Test-Path -LiteralPath $root)) { continue }
    $direct = Join-Path $root "Interface/AddOns"
    if (Test-Path -LiteralPath $direct) {
      # A client folder pointed at directly can name its own flavor
      # (/.../_retail_ is retail, not the default direct flavor classic).
      $leaf = Split-Path -Leaf $root
      $flavor = if ($FlavorDirs.ContainsKey($leaf)) { $FlavorDirs[$leaf] } else { $DirectFlavor }
      $out += [pscustomobject]@{
        Root = $root; Flavor = $flavor; AddOns = $direct
        Wtf = Join-Path $root "WTF"
      }
      continue
    }
    foreach ($dir in $FlavorDirs.Keys) {
      $path = Join-Path $root "$dir/Interface/AddOns"
      if (Test-Path -LiteralPath $path) {
        $out += [pscustomobject]@{
          Root = $root; Flavor = $FlavorDirs[$dir]; AddOns = $path
          Wtf = Join-Path $root "$dir/WTF"
        }
      }
    }
  }
  return $out
}

function Get-LastPlay([string]$WtfPath) {
  $best = $null
  foreach ($p in @($WtfPath, (Join-Path $WtfPath "Config.wtf"))) {
    if (Test-Path -LiteralPath $p) {
      $t = (Get-Item -LiteralPath $p).LastWriteTime
      if (!$best -or $t -gt $best) { $best = $t }
    }
  }
  if (Test-Path -LiteralPath $WtfPath) {
    # Directory mtimes move when the client creates cache/account dirs;
    # file mtimes move when it rewrites configs. Either signals play.
    Get-ChildItem -LiteralPath $WtfPath -ErrorAction SilentlyContinue |
      ForEach-Object { if ($_.LastWriteTime -gt $best) { $best = $_.LastWriteTime } }
    # A session that changes no setting may leave the top level alone, but
    # logging out rewrites SavedVariables (account and per character) and the
    # account's *-cache.wtf files. Bounded: Account/<a>[/<realm>/<char>].
    $paths = @()
    foreach ($a in @(Get-ChildItem -LiteralPath (Join-Path $WtfPath "Account") -Directory -ErrorAction SilentlyContinue)) {
      $paths += @(Get-ChildItem -LiteralPath $a.FullName -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
      $paths += Join-Path $a.FullName "SavedVariables"
      foreach ($realm in @(Get-ChildItem -LiteralPath $a.FullName -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne "SavedVariables" })) {
        foreach ($char in @(Get-ChildItem -LiteralPath $realm.FullName -Directory -ErrorAction SilentlyContinue)) {
          $paths += Join-Path $char.FullName "SavedVariables"
        }
      }
    }
    foreach ($p in $paths) {
      if (Test-Path -LiteralPath $p) {
        $t = (Get-Item -LiteralPath $p).LastWriteTime
        if ($t -gt $best) { $best = $t }
      }
    }
  }
  return $best
}

function Test-Managed([string]$AddOns) {
  # A previous sync left this marker: keep managing it even if idle.
  return @(Get-ChildItem -LiteralPath $AddOns -Directory `
    -ErrorAction SilentlyContinue | Where-Object {
      Test-Path -LiteralPath (Join-Path $_.FullName ".synced-version")
    }).Count -gt 0
}

function Test-TargetActive($Target) {
  if (Test-ListMatch $Exclude $Target) { return $false }
  if (Test-ListMatch $Include $Target) { return $true }
  if (Test-Managed $Target.AddOns) { return $true }
  if ($ActiveWithinDays -le 0) { return $true }
  $last = Get-LastPlay $Target.Wtf
  if ($last -and ((Get-Date) - $last).TotalDays -le $ActiveWithinDays) { return $true }
  return $false
}

function Invoke-Download([string]$Url, [string]$Destination) {
  $last = $null
  for ($i = 1; $i -le 3; $i++) {
    try {
      Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing -TimeoutSec 300
      return
    } catch {
      $last = $_
      Start-Sleep -Seconds (3 * $i)
    }
  }
  throw "download failed: $Url ($last)"
}

# ---------------------------------------------------------------- main

function Test-HttpBase([string]$Base) { return $Base -match '^https?://' }

function Read-PublishManifest([string]$Base) {
  if (!(Test-HttpBase $Base)) {
    # A folder or share. When it is down, say so; do not retry it as a URL.
    $file = Join-Path $Base "manifest.json"
    if (!(Test-Path -LiteralPath $Base)) {
      Write-Log "ERROR: publish folder not reachable: $Base"
      return $null
    }
    try {
      return Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
      Write-Log "ERROR: cannot read $file : $($_.Exception.Message)"
      return $null
    }
  }
  $url = "$Base/manifest.json"
  try {
    return Invoke-RestMethod -Uri $url -TimeoutSec 30 -UseBasicParsing
  } catch {
    Write-Log "ERROR: cannot read $url : $($_.Exception.Message)"
    return $null
  }
}

function Get-FlavorZip([string]$Base, [string]$Flavor, [string]$Name,
  [string]$Destination) {
  if (!(Test-HttpBase $Base)) {
    Copy-Item -LiteralPath (Join-Path $Base "$Flavor/$Name") `
      -Destination $Destination -Force
    return
  }
  $url = "{0}/{1}/{2}" -f $Base, [uri]::EscapeDataString($Flavor), [uri]::EscapeDataString($Name)
  Invoke-Download $url $Destination
}

function Test-LeafName($Value) {
  # addon and zip are joined onto local paths: a bare file name only.
  $s = "$Value"
  return $s -and $s -ne "." -and $s -ne ".." -and ([IO.Path]::GetFileName($s) -eq $s)
}

Write-Log "=== addon sync start (from $ManifestBase) ==="
$manifest = Read-PublishManifest $ManifestBase
if (!$manifest -or !$manifest.flavors) {
  Write-Log "ERROR: no flavors in the manifest"
  exit 1
}

# The five built-in flavors keep their order; a preset may publish more, each
# carrying its client folder as wow_dir. Without this, an extra flavor would
# be published and silently never installed.
foreach ($p in @($manifest.flavors.PSObject.Properties)) {
  # Both names become local paths (the install target and the backup folder),
  # so a manifest may only use plain folder names here.
  $wowDir = "$($p.Value.wow_dir)"
  if ($p.Name -notmatch '^[A-Za-z0-9_-]+$' -or ($wowDir -and $wowDir -notmatch '^[A-Za-z0-9_]+$')) {
    Write-Log ("ERROR: flavor {0} (wow_dir {1}) is not a plain folder name; skipped" -f $p.Name, $wowDir)
    continue
  }
  if ($wowDir -and !($FlavorDirs.Values -contains $p.Name)) {
    if ($FlavorDirs.ContainsKey($wowDir)) {
      Write-Log ("note: flavor {0} wants {1}, which belongs to flavor {2}; skipped" -f $p.Name, $wowDir, $FlavorDirs[$wowDir])
      continue
    }
    $FlavorDirs[$wowDir] = $p.Name
  }
  if ($FlavorOrder -notcontains $p.Name) {
    if ($FlavorDirs.Values -contains $p.Name) { $FlavorOrder += $p.Name }
    else { Write-Log ("note: flavor {0} has no wow_dir in the manifest; skipped" -f $p.Name) }
  }
}

$found = @(Get-WowTargets)
if ($found.Count -eq 0) {
  Write-Log "No WoW installs found under: $($Roots -join ', ')"
  exit 0
}
$targets = @($found | Where-Object { Test-TargetActive $_ })
foreach ($t in @($found | Where-Object { -not (Test-TargetActive $_) })) {
  $last = Get-LastPlay $t.Wtf
  $when = if ($last) { $last.ToString("yyyy-MM-dd") } else { "never played" }
  Write-Log ("inactive, skipping: {0} at {1} (WTF {2})" -f $t.Flavor, $t.AddOns, $when)
}
if ($targets.Count -eq 0) {
  Write-Log "No active installs (use -Include to force one)."
  exit 0
}
Write-Log ("targets: " + (($targets | ForEach-Object { "$($_.Flavor)@$($_.AddOns)" }) -join "; "))

$changed = 0
$failed = 0
foreach ($flavor in $FlavorOrder) {
  $entry = $manifest.flavors.$flavor
  if (!$entry) { continue }
  $mine = @($targets | Where-Object { $_.Flavor -eq $flavor })
  if ($mine.Count -eq 0) { continue }

  # The publisher always writes these; anything else is a broken or
  # hand-edited manifest, and installing from it would skip verification.
  $bad = @()
  if (!(Test-LeafName $entry.addon)) { $bad += "addon" }
  if (!(Test-LeafName $entry.zip)) { $bad += "zip" }
  if (!"$($entry.version)") { $bad += "version" }
  if ("$($entry.sha256)" -notmatch '^[0-9a-fA-F]{64}$') { $bad += "sha256" }
  if ($bad.Count) {
    Write-Log ("ERROR: {0}: manifest entry has a missing or invalid {1}; not installing" -f $flavor, ($bad -join ", "))
    $failed += $mine.Count
    continue
  }

  foreach ($target in $mine) {
    $addonPath = Join-Path $target.AddOns $entry.addon
    $local = Get-MarkerVersion $addonPath
    if ($local) {
      if (!$Force -and !(Test-Newer $entry.version $local)) {
        Write-Log ("{0}: up to date ({1}) at {2}" -f $flavor, $local, $target.AddOns)
        continue
      }
    } elseif (!$Force) {
      # Not installed by a sync. The toc often carries only major.minor
      # ("11.2" vs "11.2.0" compare equal), so equal means "take it over";
      # only a strictly newer hand-installed copy is left alone.
      $tocVersion = Get-TocVersion $addonPath
      if ($tocVersion -and (Test-Newer $tocVersion $entry.version)) {
        Write-Log ("{0}: local copy {1} is newer than {2}; leaving it at {3}" -f $flavor, $tocVersion, $entry.version, $target.AddOns)
        continue
      }
    }
    $label = "{0} {1} -> {2} ({3})" -f $flavor, $entry.version, $target.AddOns, $target.Root
    if (!$PSCmdlet.ShouldProcess($target.AddOns, "install $($entry.addon) $($entry.version)")) {
      Write-Log "WHATIF: $label"
      continue
    }

    $tmpZip = Join-Path $TempDir ("addon-sync-{0}.zip" -f [guid]::NewGuid().ToString("N"))
    $tmpDir = Join-Path $TempDir ("addon-sync-{0}" -f [guid]::NewGuid().ToString("N"))
    try {
      Get-FlavorZip $ManifestBase $flavor $entry.zip $tmpZip
      $hash = (Get-FileHash -LiteralPath $tmpZip -Algorithm SHA256).Hash.ToLower()
      if ($hash -ne "$($entry.sha256)".ToLower()) {
        throw "sha256 mismatch (got $hash, expected $($entry.sha256))"
      }
      New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
      # Expand-Archive skips an entry that escapes the folder with only a
      # non-terminating error; stop instead of installing a partial addon.
      Expand-Archive -LiteralPath $tmpZip -DestinationPath $tmpDir -Force -ErrorAction Stop

      $toc = Get-ChildItem -LiteralPath $tmpDir -Recurse -Filter "*.toc" |
        Where-Object { $_.BaseName -eq $entry.addon } |
        Select-Object -First 1
      if (!$toc) { throw "no $($entry.addon).toc in the zip" }
      $src = $toc.Directory.FullName
      if ((Split-Path -Leaf $src) -ne $entry.addon) {
        Write-Log ("note: zip folder is {0}, expected {1}; using the zip" -f (Split-Path -Leaf $src), $entry.addon)
      }

      # Unique per target: two installs of one flavor updated in the same
      # second must not share (and nest inside) one backup folder. The name
      # still sorts by time for the prune below.
      $stamp = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), [guid]::NewGuid().ToString("N").Substring(0, 6)
      $backup = Join-Path $BackupDir ("AddOns/{0}/{1}" -f $flavor, $stamp)
      $movedAway = $false
      try {
        if (Test-Path -LiteralPath $addonPath) {
          New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
          Move-Item -LiteralPath $addonPath -Destination $backup -Force
          $movedAway = $true
          Write-Log "backup: $backup"
        }
        Copy-Item -LiteralPath $src -Destination $addonPath -Recurse -Force
        Set-Content -LiteralPath (Join-Path $addonPath ".synced-version") `
          -Value $entry.version -Encoding ASCII
      } catch {
        $why = $_
        if ($movedAway) {
          # Clear the half-copied folder first; if that fails, moving the
          # backup "onto" it would nest it inside instead of restoring it.
          if (Test-Path -LiteralPath $addonPath) {
            Remove-Item -LiteralPath $addonPath -Recurse -Force -ErrorAction SilentlyContinue
          }
          if (Test-Path -LiteralPath $addonPath) {
            Write-Log "ERROR: could not clear $addonPath; previous addon left at $backup"
          } else {
            Move-Item -LiteralPath $backup -Destination $addonPath -Force
            Write-Log "restored previous addon after a failed install"
          }
        }
        throw $why
      }
      $changed++
      Write-Log "installed: $label"
    } catch {
      $failed++
      Write-Log "ERROR: $label : $($_.Exception.Message)"
    } finally {
      Remove-Item -LiteralPath $tmpZip -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
}

# prune old backups: keep the newest 5 per flavor
foreach ($flavor in $FlavorDirs.Values | Select-Object -Unique) {
  $dir = Join-Path $BackupDir "AddOns/$flavor"
  if (!(Test-Path -LiteralPath $dir)) { continue }
  Get-ChildItem -LiteralPath $dir -Directory |
    Sort-Object Name -Descending |
    Select-Object -Skip 5 |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}

if ($failed) {
  # Non-zero so Task Scheduler / systemd show the failure, not just the log.
  Write-Log "=== addon sync done: $changed installed, $failed FAILED ==="
  exit 1
}
Write-Log "=== addon sync done: $changed installed ==="
exit 0
