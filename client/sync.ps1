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
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
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
  $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
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

function Write-Log([string]$Message) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  $dir = Split-Path -Parent $LogPath
  if ($dir -and !(Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  # Local shadow: -WhatIf must still produce a log to review afterwards.
  $WhatIfPreference = $false
  Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
  Write-Host $line
}

function ConvertTo-Version([string]$Text) {
  if (!$Text) { return @() }
  return @([regex]::Matches($Text, "\d+") | ForEach-Object { [int]$_.Value })
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

function Get-LocalVersion([string]$AddonPath) {
  $marker = Join-Path $AddonPath ".synced-version"
  if (Test-Path $marker) {
    $v = (Get-Content -LiteralPath $marker -TotalCount 1).Trim()
    if ($v) { return $v }
  }
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
      $out += [pscustomobject]@{
        Root = $root; Flavor = $DirectFlavor; AddOns = $direct
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
  if ($Exclude -contains $Target.Flavor -or $Exclude -contains $Target.Root) { return $false }
  if ($Include -contains $Target.Flavor -or $Include -contains $Target.Root) { return $true }
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

function Read-PublishManifest([string]$Base) {
  if (Test-Path -LiteralPath $Base) {
    $file = Join-Path $Base "manifest.json"
    try {
      return Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
    } catch {
      Write-Log "ERROR: cannot read $file : $($_.Exception.Message)"
      return $null
    }
  }
  $url = "$Base/manifest.json"
  try {
    return Invoke-RestMethod -Uri $url -TimeoutSec 30
  } catch {
    Write-Log "ERROR: cannot read $url : $($_.Exception.Message)"
    return $null
  }
}

function Get-FlavorZip([string]$Base, [string]$Flavor, [string]$Name,
  [string]$Destination) {
  if (Test-Path -LiteralPath $Base) {
    Copy-Item -LiteralPath (Join-Path $Base "$Flavor/$Name") `
      -Destination $Destination -Force
    return
  }
  Invoke-Download "$Base/$Flavor/$Name" $Destination
}

Write-Log "=== addon sync start (from $ManifestBase) ==="
$manifest = Read-PublishManifest $ManifestBase
if (!$manifest -or !$manifest.flavors) {
  Write-Log "ERROR: no flavors in the manifest"
  exit 1
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
foreach ($flavor in $FlavorOrder) {
  $entry = $manifest.flavors.$flavor
  if (!$entry) { continue }
  $mine = @($targets | Where-Object { $_.Flavor -eq $flavor })
  if ($mine.Count -eq 0) { continue }

  foreach ($target in $mine) {
    $addonPath = Join-Path $target.AddOns $entry.addon
    $local = Get-LocalVersion $addonPath
    if (!$Force -and !(Test-Newer $entry.version $local)) {
      Write-Log ("{0}: up to date ({1}) at {2}" -f $flavor, $local, $target.AddOns)
      continue
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
      if ($entry.sha256 -and $hash -ne $entry.sha256.ToLower()) {
        throw "sha256 mismatch (got $hash, expected $($entry.sha256))"
      }
      New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
      Expand-Archive -LiteralPath $tmpZip -DestinationPath $tmpDir -Force

      $toc = Get-ChildItem -LiteralPath $tmpDir -Recurse -Filter "*.toc" |
        Where-Object { $_.BaseName -eq $entry.addon } |
        Select-Object -First 1
      if (!$toc) { throw "no $($entry.addon).toc in the zip" }
      $src = $toc.Directory.FullName
      if ((Split-Path -Leaf $src) -ne $entry.addon) {
        Write-Log ("note: zip folder is {0}, expected {1}; using the zip" -f (Split-Path -Leaf $src), $entry.addon)
      }

      $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
      $backup = Join-Path $BackupDir ("AddOns/{0}/{1}" -f $flavor, $stamp)
      if (Test-Path -LiteralPath $addonPath) {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
        Move-Item -LiteralPath $addonPath -Destination $backup -Force
        Write-Log "backup: $backup"
      }

      try {
        Copy-Item -LiteralPath $src -Destination $addonPath -Recurse -Force
        Set-Content -LiteralPath (Join-Path $addonPath ".synced-version") `
          -Value $entry.version -Encoding ASCII
      } catch {
        Remove-Item -LiteralPath $addonPath -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $backup) {
          Move-Item -LiteralPath $backup -Destination $addonPath -Force
          Write-Log "restored previous addon after a failed copy"
        }
        throw
      }
      $changed++
      Write-Log "installed: $label"
    } catch {
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

Write-Log "=== addon sync done: $changed installed ==="
exit 0
