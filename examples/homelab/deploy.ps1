<#
.SYNOPSIS
  Deploy the portal-hosted Addon sync worker (example: one person's setup).

.DESCRIPTION
  Idempotent. Pushes the worker files, installs the packages the browser
  reader needs, migrates the portal to the plugin loader (with a backup),
  restarts the portal, and publishes whatever Addon archives are already
  on the feed host.

  This is the deployment for the example hosted setup in this folder, not
  the normal path: most users run feed/install.sh on the feed host itself.
  Connection settings come from deploy.config.json next to this script
  (copy deploy.config.example.json to start), or from the parameters below,

  Connection settings come from deploy.config.json next to this script
  (copy deploy.config.example.json to start), or from the parameters below,
  which override the file. deploy.config.json is gitignored: secrets and
  hostnames never live in this repo.

  The browser profile keeps the forum session. If the portal reports
  a login is needed, sign in once (see README).

.EXAMPLE
  .\deploy.ps1
  .\deploy.ps1 -SkipPackages
  .\deploy.ps1 -PiHost 192.168.1.10 -NoImport
#>
[CmdletBinding()]
param(
  [string]$PiHost = "",
  [string]$PiUser = "",
  [string]$HostKey = "",
  [string]$PwFile = "",
  [string]$Plink = "",
  [string]$PortalUrl = "",
  [string]$Config = "",
  [switch]$SkipPackages,
  [switch]$NoRestart,
  [switch]$NoImport
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

# Fill any unset parameter from deploy.config.json (gitignored local config).
$cfgPath = if ($Config) { $Config } else { Join-Path $here "deploy.config.json" }
if (Test-Path -LiteralPath $cfgPath) {
  $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
  foreach ($name in @("PiHost", "PiUser", "HostKey", "PwFile", "Plink", "PortalUrl")) {
    if (-not $PSBoundParameters.ContainsKey($name) -and $cfg.$name) {
      Set-Variable -Name $name -Value ([string]$cfg.$name)
    }
  }
}
foreach ($need in @("PiHost", "PiUser", "HostKey", "PwFile", "Plink")) {
  if (-not (Get-Variable -Name $need -ValueOnly)) {
    throw ("{0} is not set. Copy deploy.config.example.json to deploy.config.json and fill it in, or pass -{0} explicitly." -f $need)
  }
}
if (!$PortalUrl) { $PortalUrl = ("http://{0}:8088/addons" -f $PiHost) }
$remote = "/usr/local/libexec/cellproxy"

function Invoke-Pi([string]$Script) {
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script))
  $out = & $Plink -ssh -batch -hostkey $HostKey -pwfile $PwFile "$PiUser@$PiHost" "echo $b64 | base64 -d | bash" 2>&1
  if ($LASTEXITCODE -ne 0) { throw "remote command failed:`n$($out -join "`n")" }
  return $out
}

function Push-File([string]$Local, [string]$RemotePath) {
  $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($Local))
  # plink on this host drops the remote command somewhere above 8 KB; keep
  # each base64 slice well under that.
  $chunk = 6000
  $first = $true
  for ($i = 0; $i -lt $b64.Length; $i += $chunk) {
    $part = $b64.Substring($i, [Math]::Min($chunk, $b64.Length - $i))
    $op = if ($first) { ">" } else { ">>" }
    Invoke-Pi "printf %s '$part' | base64 -d $op '$RemotePath'" | Out-Null
    $first = $false
  }
  Write-Host ("pushed {0} -> {1} ({2:N0} bytes" -f (Split-Path -Leaf $Local), $RemotePath, (Get-Item $Local).Length) + ")"
}

Write-Host "== dirs and packages"
Invoke-Pi @'
set -e
install -d -o root -g cellproxy -m 2775 /var/lib/cellproxy/addons
install -d -o root -g cellproxy -m 2775 /var/lib/cellproxy/addons/stage
install -d -o root -g cellproxy -m 2775 /var/lib/cellproxy/addons/cache
install -d -o root -g cellproxy -m 2775 /mnt/ssd/addons
for pkg in unrar chromium xvfb x11vnc; do
  case $pkg in
    chromium) bin=chromium ;;
    xvfb) bin=Xvfb ;;
    x11vnc) bin=x11vnc ;;
    unrar) bin=unrar ;;
  esac
  if ! command -v $bin >/dev/null 2>&1; then
    need="$need $pkg"
  fi
done
if [ -n "$need" ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need >/tmp/addons-apt.log 2>&1 || tail -5 /tmp/addons-apt.log
fi
command -v unrar >/dev/null && echo HAVE_UNRAR || echo "WARN: unrar missing"
command -v chromium >/dev/null && echo HAVE_CHROMIUM || echo "WARN: chromium missing"
command -v Xvfb >/dev/null && echo HAVE_XVFB || echo "WARN: xvfb missing"
'@ | Out-Null

if (!$SkipPackages) {
  # stdlib only now; nothing to pip-install.
  Invoke-Pi "command -v unrar >/dev/null && echo HAVE_UNRAR || echo NO_UNRAR" | Out-Null
}

Write-Host "== worker, browser reader and plugin"
$feed = Join-Path $here "..\..\feed"
Push-File (Join-Path $feed "worker.py") "$remote/worker.py"
Push-File (Join-Path $feed "reader.py") "$remote/reader.py"
Push-File (Join-Path $feed "addonsync_common.py") "$remote/addonsync_common.py"
Push-File (Join-Path $here "patch_portal_plugins.py") "$remote/patch_portal_plugins.py"
Invoke-Pi "install -d $remote/plugins"
Push-File (Join-Path $here "plugins\__init__.py") "$remote/plugins/__init__.py"
Push-File (Join-Path $here "plugins\addons.py") "$remote/plugins/addons.py"
Invoke-Pi "chmod 755 $remote/worker.py $remote/reader.py $remote/patch_portal_plugins.py; python3 -m py_compile $remote/worker.py $remote/reader.py $remote/addonsync_common.py $remote/plugins/__init__.py $remote/plugins/addons.py && echo worker-ok" | Out-Null

Push-File (Join-Path $here "cellproxy-addons.service") "/etc/systemd/system/cellproxy-addons.service"
Push-File (Join-Path $here "cellproxy-addons.timer") "/etc/systemd/system/cellproxy-addons.timer"

$conf = Join-Path $feed "config.example.json"
Push-File $conf "/tmp/addons-config.json"
Invoke-Pi "if [ ! -f /etc/cellproxy/addons.json ]; then install -m 644 /tmp/addons-config.json /etc/cellproxy/addons.json; echo config-installed; else echo config-kept; fi; rm -f /tmp/addons-config.json" | Out-Null
# migrate the manifest path off root-only /etc, add browser defaults
Invoke-Pi @'
python3 - <<'PYEOF'
import json
p='/etc/cellproxy/addons.json'
d=json.load(open(p))
changed=False
if d.get('manifest_file','').startswith('/etc/'):
    d['manifest_file']='/var/lib/cellproxy/addons/manifest.json'; changed=True
defaults={"browser_profile":"/var/lib/cellproxy/addons/browser-profile",
          "browser_port":9333,"browser_display":":99","browser_timeout":60,
          "check_hours":24}
for k,v in defaults.items():
    if k not in d: d[k]=v; changed=True
if d.get('check_hours')==12:
    d['check_hours']=24; changed=True
if changed:
    json.dump(d,open(p,'w'),indent=2); print('config-migrated')
else:
    print('config-current')
PYEOF
'@ | Out-Null

Write-Host "== portal plugin migration"
$mig = Invoke-Pi "python3 $remote/patch_portal_plugins.py; python3 -m py_compile $remote/portal.py && echo portal-ok"
$mig | ForEach-Object { Write-Host $_ }

Write-Host "== retire trigger path units"
Invoke-Pi @'
systemctl disable --now cellproxy-addons.path cellproxy-addons-trigger.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/cellproxy-addons.path /etc/systemd/system/cellproxy-addons-trigger.service
systemctl daemon-reload
systemctl enable --now cellproxy-addons.timer >/dev/null 2>&1
systemctl is-enabled cellproxy-addons.timer
systemctl is-active cellproxy-addons.timer
'@ | ForEach-Object { Write-Host $_ }

if (!$NoRestart) {
  Invoke-Pi "systemctl restart cellproxy-web; sleep 1; systemctl is-active cellproxy-web" | ForEach-Object { Write-Host "cellproxy-web: $_" }
}

Write-Host "== browser warm-up"
Invoke-Pi "python3 $remote/reader.py browser 2>&1 | tail -5; python3 $remote/reader.py status 2>&1 | tail -12" | ForEach-Object { Write-Host $_ }

if (!$NoImport) {
  Write-Host "== first publish from existing archives"
  Invoke-Pi "python3 $remote/worker.py import 2>&1 | tail -40" | ForEach-Object { Write-Host $_ }
}

Write-Host "== status"
Invoke-Pi "python3 $remote/worker.py status" | ForEach-Object { Write-Host $_ }
Write-Host "`nPortal: $PortalUrl"
