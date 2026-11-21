<#
.SYNOPSIS
  Export the forum's cookies from a browser to a JSON file.

.DESCRIPTION
  One-time helper. Opens a browser on the feed's thread, lets you sign in,
  then dumps the cookies over the DevTools protocol and saves the forum's
  and Cloudflare's ones. Copy the file to the feed host and run
  `reader.py import-cookies` so the feed browser inherits the session
  instead of needing an interactive sign-in.

  The thread comes from the same feed preset the worker reads
  (feeds/<name>.json), so there is one place to change it.

  Run this on a machine with a desktop. It uses a throwaway profile, not
  your everyday browser, so the sign-in you do here is the one that gets
  exported. Delete the JSON after importing it.

.PARAMETER Preset
  Feed preset to read the thread URL from. Defaults to the preset in
  feed/config.json, then ADDONSYNC_PRESET.

.PARAMETER ThreadUrl
  Override the thread URL instead of taking it from the preset.

.PARAMETER NoWait
  Skip the sign-in prompt and dump immediately. Use when the profile
  already holds a signed-in session (a repeat export).

.EXAMPLE
  .\Export-Cookies.ps1
  .\Export-Cookies.ps1 -BrowserExe "C:\Program Files\Chromium\chrome.exe"
  .\Export-Cookies.ps1 -ThreadUrl "https://forum.example.com/threads/1"
#>
[CmdletBinding()]
param(
  [string]$Profile = "",
  [string]$Out = "",
  [string]$BrowserExe = "",
  [string]$Preset = "",
  [string]$ThreadUrl = "",
  [int]$Port = 9334,
  [switch]$NoWait
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

if (!$ThreadUrl) {
  if (!$Preset) {
    $cfgPath = Join-Path $repo "feed/config.json"
    if (Test-Path -LiteralPath $cfgPath) {
      $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
      $Preset = [string]$cfg.preset
    }
    if (!$Preset) { $Preset = $env:ADDONSYNC_PRESET }
    if (!$Preset) { $Preset = "generic-example" }
  }
  $cand = @(
    (Join-Path $repo "feeds/$Preset.json"),
    (Join-Path $PSScriptRoot "feeds/$Preset.json")
  ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  if (!$cand) {
    throw "no feeds/$Preset.json; pass -Preset <name> or -ThreadUrl <url>"
  }
  $feed = Get-Content -LiteralPath $cand -Raw -Encoding UTF8 | ConvertFrom-Json
  $ThreadUrl = [string]$feed.thread_url
  if (!$ThreadUrl) { throw "$cand has no thread_url; pass -ThreadUrl <url>" }
}
$site = ([Uri]$ThreadUrl).Host -replace '^www\.', ''
if (!$site) { throw "cannot read a host from $ThreadUrl" }

if (!$Profile) {
  $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA }
    elseif ($IsMacOS) { Join-Path $HOME "Library/Application Support" }
    elseif ($env:XDG_DATA_HOME) { $env:XDG_DATA_HOME }
    else { Join-Path $HOME ".local/share" }
  $Profile = Join-Path $base "AddonSync/browser-profile"
}
if (!$Out) {
  $tmp = if ($env:TEMP) { $env:TEMP } else { [IO.Path]::GetTempPath() }
  $Out = Join-Path $tmp "addon-cookies.json"
}

function Find-Browser {
  if ($BrowserExe) {
    if (!(Test-Path -LiteralPath $BrowserExe)) { throw "browser not found: $BrowserExe" }
    return $BrowserExe
  }
  $cands = @(
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Chromium\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (!$cands) {
    foreach ($name in @("google-chrome", "chromium", "chromium-browser", "chrome")) {
      $cmd = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($cmd) { $cands = $cmd.Source; break }
    }
  }
  if (-not $cands) { throw ("no browser found. Pass -BrowserExe with the path to Edge, Chrome or Chromium.") }
  return [string]$cands
}

function Send-Cdp($ws, $obj) {
  $b = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress -Depth 8))
  $ws.SendAsync([ArraySegment[byte]]::new($b),
                [Net.WebSockets.WebSocketMessageType]::Text, $true,
                [Threading.CancellationToken]::None).Wait(8000) | Out-Null
}

function Receive-Cdp($ws, [int]$id, [int]$timeoutMs = 20000) {
  $buf = New-Object byte[] 262144
  $ms = New-Object IO.MemoryStream
  $deadline = (Get-Date).AddMilliseconds($timeoutMs)
  while ((Get-Date) -lt $deadline) {
    $seg = [ArraySegment[byte]]::new($buf)
    $t = $ws.ReceiveAsync($seg, [Threading.CancellationToken]::None)
    if (-not $t.Wait(3000)) { continue }
    $ms.Write($buf, 0, $t.Result.Count)
    if ($t.Result.EndOfMessage) {
      $s = [Text.Encoding]::UTF8.GetString($ms.ToArray())
      $ms.SetLength(0)
      try { $o = $s | ConvertFrom-Json } catch { continue }
      if ($o.id -eq $id) { return $o }
    }
  }
  return $null
}

$exe = Find-Browser
# Quote the profile path: the macOS default lives under "Application Support",
# and an unquoted --user-data-dir would be split at the space.
$proc = Start-Process -FilePath $exe -ArgumentList @(
  "--remote-debugging-port=$Port",
  ('--user-data-dir="{0}"' -f $Profile),
  "--no-first-run", "--no-default-browser-check", $ThreadUrl) -PassThru
try {
  $page = $null
  $deadline = (Get-Date).AddSeconds(45)
  while (!$page -and (Get-Date) -lt $deadline) {
    try {
      # Invoke-RestMethod in PS 5.1 pipes a JSON array as one item, which
      # made this pick a concatenation of extension and tab URLs. Parse the
      # raw body instead.
      $json = (Invoke-WebRequest "http://127.0.0.1:$Port/json" -UseBasicParsing).Content
      $pages = @($json | ConvertFrom-Json)
      $page = $pages | Where-Object {
        $_.type -eq "page" -and $_.webSocketDebuggerUrl -and
        $_.url -like "*$site*"
      } | Select-Object -First 1
    } catch { }
    if (!$page) { Start-Sleep -Milliseconds 750 }
  }
  if (-not $page) {
    throw "the browser never loaded the thread; open it in the window and retry"
  }
  if (!$NoWait) {
    Write-Host ""
    Write-Host "A browser window opened on the feed thread:"
    Write-Host "  $ThreadUrl"
    Write-Host "Sign in to $site in that window (not your usual browser)."
    Read-Host "Press Enter here once the thread shows the post attachments" | Out-Null
  }
  $wsUrl = [string]($page.webSocketDebuggerUrl | Select-Object -First 1)
  $ws = New-Object System.Net.WebSockets.ClientWebSocket
  $ws.ConnectAsync([Uri]$wsUrl,
                   [Threading.CancellationToken]::None).Wait(8000) | Out-Null
  $keep = @()
  $id = 0
  for ($try = 1; $try -le 15; $try++) {
    $id++
    Send-Cdp $ws @{ id = $id; method = "Storage.getCookies" }
    $r = Receive-Cdp $ws $id
    if ($r -and $r.result.cookies) {
      $keep = @($r.result.cookies | Where-Object {
        $_.domain -like "*$site*" -or $_.domain -like "*cloudflare*"
      })
    }
    if ($keep.Count -gt 0) { break }
    if ($try -eq 1) { Write-Host "waiting for the thread to set its cookies..." }
    Start-Sleep -Seconds 2
  }
  $ws.CloseAsync([Net.WebSockets.WebSocketCloseStatus]::NormalClosure,
                 "done", [Threading.CancellationToken]::None).Wait(3000) | Out-Null
  if ($keep.Count -eq 0) {
    throw ("no $site cookies found. Sign in inside the window this " +
      "script opened, open the thread, then run it again.")
  }
  $keep | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Out -Encoding UTF8
  Write-Host ("saved {0} cookies to {1}" -f $keep.Count, $Out)
  $keep | ForEach-Object { Write-Host ("  {0} domain={1} expires={2}" -f $_.name, $_.domain, $_.expires) }
  if (@($keep | Where-Object { $_.name -match "(?i)userid|session" }).Count -eq 0) {
    Write-Host "note: no forum session cookie in that dump (Cloudflare only?)."
    Write-Host "      If import-cookies says the thread shows no attachments,"
    Write-Host "      sign in in the window, then run this again."
  }
  Write-Host "Delete that file after reader.py import-cookies."
} finally {
  try { $proc.Kill() } catch { }
}
