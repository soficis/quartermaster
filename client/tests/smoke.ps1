<#
.SYNOPSIS
  End-to-end test of client/sync.ps1 against a local publish folder.

.DESCRIPTION
  Builds a throwaway publish folder and a fake WoW install, then drives the
  real script as a child process. Covers: -WhatIf writes nothing, install,
  "up to date", upgrade with backup, corrupt sha256 leaves the old addon in
  place, and Setup.ps1 -NoSchedule stops after writing the config.

  No network, no scheduler, no real game folder. Exits 1 when a check fails.

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

function New-Publish([string]$Version, [string]$Sha = "", [bool]$Corrupt = $false) {
  Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $src | Out-Null
  Write-Utf8 (Join-Path $src "DemoAddon.toc") "## Interface: 110200`n## Version: $Version`n"
  Write-Utf8 (Join-Path $src "core.lua") "-- version $Version`n"

  $flavorDir = Join-Path $publish "retail"
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
  $manifest = [ordered]@{
    generated = [int](New-TimeSpan -Start ([datetime]"1970-01-01") -End (Get-Date)).TotalSeconds
    flavors   = @{ retail = $entry }
  }
  Write-Utf8 (Join-Path $publish "manifest.json") ($manifest | ConvertTo-Json -Depth 6)
}

function Invoke-Sync([string[]]$Extra = @()) {
  $out = & $hostExe -NoProfile -ExecutionPolicy Bypass -File $sync `
    -Config $cfgPath @Extra 2>&1
  [pscustomobject]@{ Code = $LASTEXITCODE; Output = (@($out) -join "`n") }
}

function Read-Text([string]$Path) {
  if (Test-Path -LiteralPath $Path) { return (Get-Content -LiteralPath $Path -Raw) }
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

  $setupCopy = Join-Path $root "client"
  New-Item -ItemType Directory -Force -Path $setupCopy | Out-Null
  Copy-Item -LiteralPath $setup -Destination $setupCopy
  $out = & $hostExe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $setupCopy "Setup.ps1") `
    -ManifestBase $publish -Roots $wow -Yes -NoSchedule 2>&1
  $setupCfg = Join-Path $setupCopy "sync.config.json"
  Check ($LASTEXITCODE -eq 0) "setup -NoSchedule exits 0"
  Check (Test-Path -LiteralPath $setupCfg) "setup writes the config"
  Check ((@($out) -join "`n") -match "no scheduler registered") "setup stops before the scheduler"
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
