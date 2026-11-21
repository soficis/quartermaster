<#
.SYNOPSIS
  First-run setup: write client/sync.config.json, optionally schedule it.

.DESCRIPTION
  Asks for the publish location (a local folder or http(s) URL serving the
  feed worker's publish directory) and your WoW install folders. Installs
  found at the per-OS guesses are detected and offered for confirmation;
  a folder that is not a WoW layout gets a warning. Writes sync.config.json
  next to sync.ps1, then offers to register the platform scheduler.
  Fully non-interactive when every value is passed as a parameter.

.EXAMPLE
  .\Setup.ps1
  .\Setup.ps1 -ManifestBase C:\AddOns\publish -Roots "D:\Games\World of Warcraft" -InstallSchedule
  .\Setup.ps1 -ManifestBase http://your-host:8088/publish -Roots "D:\Games\World of Warcraft" -InstallSchedule
#>
[CmdletBinding()]
param(
  [Alias("PiBase")]
  [string]$ManifestBase = "",
  [string[]]$Roots = @(),
  [string]$DirectFlavor = "",
  [string[]]$Times = @("07:30", "19:30"),
  [switch]$InstallSchedule,
  [switch]$NoSchedule,
  [switch]$Yes
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfgPath = Join-Path $here "sync.config.json"

# "-Roots a,b" is one string when the script is run with -File from a shell
# that does not split it (bash, cmd); accept it and a real array alike.
$Roots = @($Roots | ForEach-Object { $_ -split ',' } |
  ForEach-Object { $_.Trim() } | Where-Object { $_ })

function Resolve-Root([string]$Path) {
  # Store absolute roots: a "~/Games/..." typed on a Mac or Linux would
  # otherwise be a literal folder name to the scheduler's working directory.
  $s = "$Path".Trim()
  if ($s -match '^~([\\/]|$)') {
    $s = Join-Path $HOME $s.Substring(1).TrimStart('\', '/')
  }
  if ($s -match '%') { $s = [Environment]::ExpandEnvironmentVariables($s) }
  try { return [IO.Path]::GetFullPath($s) } catch { return $s }
}
$Roots = @($Roots | ForEach-Object { Resolve-Root $_ } | Where-Object { $_ } |
  Select-Object -Unique)

function Test-WowRoot([string]$Path) {
  # A WoW root is either a direct client (Interface/AddOns at the top) or a
  # parent holding _retail_, _classic_, _classic_era_, ... with one inside.
  if (!(Test-Path -LiteralPath $Path -PathType Container)) { return $false }
  if (Test-Path -LiteralPath (Join-Path $Path "Interface/AddOns")) { return $true }
  foreach ($d in @(Get-ChildItem -LiteralPath $Path -Directory -ErrorAction SilentlyContinue)) {
    if ($d.Name -match '^_[^/\\]+_$' -and
      (Test-Path -LiteralPath (Join-Path $d.FullName "Interface/AddOns"))) { return $true }
  }
  return $false
}

function Ask([string]$Prompt, [string]$Default) {
  if ($Default) { $Prompt += " [$Default]" }
  $a = Read-Host $Prompt
  if (!$a) { return $Default }
  return $a
}

$onWindows = !$IsLinux -and !$IsMacOS
$guesses = if ($onWindows) {
    @("C:\Program Files (x86)\World of Warcraft", "C:\Program Files\World of Warcraft",
      "C:\Games\World of Warcraft")
  }
  elseif ($IsMacOS) { @("/Applications/World of Warcraft") }
  else {
    @((Join-Path $HOME "Games/world-of-warcraft/drive_c/Program Files (x86)/World of Warcraft"),
      (Join-Path $HOME ".wine/drive_c/Program Files (x86)/World of Warcraft"))
  }

if (!$ManifestBase) {
  if ($Yes) { throw "-ManifestBase is required in non-interactive mode" }
  $ManifestBase = Ask "Publish location (local folder or URL, e.g. C:\AddOns\publish)" ""
}
$ManifestBase = $ManifestBase.TrimEnd('/', '\')
if (Test-Path -LiteralPath $ManifestBase) {
  $mf = Join-Path $ManifestBase "manifest.json"
  if (!(Test-Path -LiteralPath $mf)) {
    Write-Warning "no manifest.json in $ManifestBase (feed worker has not published yet?)"
    if (!$Yes) {
      if ((Ask "Keep going anyway? (y/N)" "") -ne "y") { exit 1 }
    }
  } else { Write-Host "publish folder OK." }
} else {
  try {
    $t = Invoke-RestMethod -Uri "$ManifestBase/manifest.json" -TimeoutSec 15 -UseBasicParsing
    if (!$t.flavors) { Write-Warning "publish answered but has no flavors" }
    else { Write-Host "publish URL OK." }
  } catch {
    Write-Warning ("cannot read {0}/manifest.json : {1}" -f $ManifestBase, $_.Exception.Message)
    if (!$Yes) {
      if ((Ask "Keep going anyway? (y/N)" "") -ne "y") { exit 1 }
    }
  }
}

foreach ($r in $Roots) {
  if ((Test-Path -LiteralPath $r) -and !(Test-WowRoot $r)) {
    Write-Warning ("not a WoW root: {0} (no Interface/AddOns in it or in a _flavor_ folder)" -f $r)
  }
}

if ($Roots.Count -eq 0) {
  if ($Yes) { throw "-Roots is required in non-interactive mode" }
  Write-Host "WoW install folders (a folder holding Interface/AddOns, or a parent holding _retail_, _classic_, ...):"
  foreach ($g in $guesses) { Write-Host ("  guess: {0}  [{1}]" -f $g, $(if (Test-Path -LiteralPath $g) { "found" } else { "not found" })) }
  # Detect installs at the per-OS guesses, but never add one without the user
  # saying so: this config decides where addons get overwritten.
  foreach ($g in $guesses) {
    if (!(Test-WowRoot $g)) { continue }
    Write-Host "detected WoW install: $g"
    if ((Ask "Add it? (Y/n)" "y") -eq "y") { $Roots += $g }
  }
  while ($true) {
    $r = Ask ("root #{0} (empty to finish)" -f ($Roots.Count + 1)) ""
    if (!$r) { break }
    $r = Resolve-Root $r
    if (!(Test-Path -LiteralPath $r)) {
      Write-Warning "not found: $r"
      if ((Ask "Use it anyway? (y/N)" "") -ne "y") { continue }
    } elseif (!(Test-WowRoot $r)) {
      Write-Warning ("not a WoW root: {0} (no Interface/AddOns in it or in a _flavor_ folder)" -f $r)
      if ((Ask "Use it anyway? (y/N)" "") -ne "y") { continue }
    }
    $Roots += $r
  }
  if ($Roots.Count -eq 0) { throw "no roots configured" }
}

# Update only what Setup asks about; Include, Exclude, BackupDir, LogPath and
# the rest of an existing config survive a re-run.
$cfgData = [ordered]@{}
if (Test-Path -LiteralPath $cfgPath) {
  try {
    $old = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in @($old.PSObject.Properties)) { $cfgData[$p.Name] = $p.Value }
  } catch {
    Write-Warning "existing $cfgPath is not valid JSON; writing a fresh one"
  }
}
$cfgData["ManifestBase"] = $ManifestBase
$cfgData["Roots"] = @($Roots)
if ($PSBoundParameters.ContainsKey("DirectFlavor") -and $DirectFlavor) {
  $cfgData["DirectFlavor"] = $DirectFlavor
}
$cfgData | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $cfgPath -Encoding UTF8
Write-Host "wrote $cfgPath"

if (!$InstallSchedule -and !$NoSchedule -and !$Yes) {
  $InstallSchedule = ((Ask "Register the scheduler (runs twice a day)? (Y/n)" "y") -eq "y")
}
if ($NoSchedule) {
  Write-Host "no scheduler registered (-NoSchedule)."
  return
}
if ($InstallSchedule -or $Yes) {
  & (Join-Path $here "Install-Schedule.ps1") -Times $Times
}
