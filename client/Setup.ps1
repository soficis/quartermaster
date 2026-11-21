<#
.SYNOPSIS
  First-run setup: write client/sync.config.json, optionally schedule it.

.DESCRIPTION
  Asks for the publish location (a local folder or http(s) URL serving the
  feed worker's publish directory) and your WoW install folders (per-OS
  guesses are offered), verifies each answer, writes sync.config.json
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

function Ask([string]$Prompt, [string]$Default) {
  if ($Default) { $Prompt += " [$Default]" }
  $a = Read-Host $Prompt
  if (!$a) { return $Default }
  return $a
}

$onWindows = !$IsLinux -and !$IsMacOS
$guesses = if ($onWindows) { @("C:\Program Files (x86)\World of Warcraft", "C:\Games\World of Warcraft") }
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

if ($Roots.Count -eq 0) {
  if ($Yes) { throw "-Roots is required in non-interactive mode" }
  Write-Host "WoW install folders (a folder holding Interface/AddOns, or a parent holding _retail_, _classic_, ...):"
  foreach ($g in $guesses) { Write-Host ("  guess: {0}  [{1}]" -f $g, $(if (Test-Path -LiteralPath $g) { "FOUND" } else { "not found" })) }
  $Roots = @()
  while ($true) {
    $r = Ask ("root #{0} (empty to finish)" -f ($Roots.Count + 1)) ""
    if (!$r) { break }
    if (!(Test-Path -LiteralPath $r)) {
      Write-Warning "not found: $r"
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
