# quartermaster

One forum URL goes in. Fresh addons land in World of Warcraft before you play.

You sign in once. quartermaster checks the page on a schedule. It downloads new
builds, verifies them, backs up your addons, then replaces the old files. It runs
while you sleep.

You bring the page, the addon folder name, and your WoW install path. The repo
brings the code. There are no binaries, no hosted feed, and no addon inside it.
You clone it and run the scripts. Transfer and local install only; nothing gets
uploaded or redistributed.

## Start here

Pick one path. Both end with a scheduled job that keeps your addons current.

### Path 1 - let an agent set it up

Use this if you already use coding agents. Paste this into Claude Code, Codex,
Cursor, or whatever you use:

> Clone `https://github.com/soficis/quartermaster` and set it up for me.
> Read `AGENTS.md` first. Then read README.md "Pick a shape" and ask me four
> things: which shape I want, where my WoW folders are, which flavors I play,
> and which page to read. Set it up, verify with `worker.py status`,
> `reader.py status` and `client/sync.ps1 -WhatIf`, and register both schedules.
> Do not commit or push.

`AGENTS.md` is the setup contract. It holds the prerequisites, the exact
commands, and what to check. The agent writes `feeds/local-<name>.json` for your
page. Git ignores files named `local-*`, so your source stays yours.

One step always needs you: **signing in to the page**. If the source sits behind
a login, only you can create that session. `tools/Export-Cookies.ps1` opens a
browser on the page and hands the session to the feed. Two minutes, once every
few months.

### Path 2 - set up one PC yourself

Use this if you do not use an agent. It takes about 20 minutes. Shape A is the
simplest: the feed and the client live on the same PC, in one local folder.

Write down four things before you start:

1. The page URL. It must list links to addon archives. A login wall is fine.
2. The addon folder name inside `Interface/AddOns`, for example `DemoAddon`.
3. The first characters of each release filename, for example `Demo`.
4. Your WoW install path. A parent holding `_retail_` and `_classic_` works, or
   a single client with `Interface/AddOns` at its top.

**Step 1. Install the tools.**

- Python 3.7 or newer. The feed uses only the standard library. On macOS and
  Linux, use `python3` wherever a command below says `python`.
- Chromium, Chrome, or Edge. The feed reads the page through a real browser.
- PowerShell 5.1 on Windows. Install PowerShell 7 on macOS or Linux
  (<https://aka.ms/ps7>). The `.tar.gz` build needs no admin rights.
- A RAR extractor only if releases are not `.zip`. Use `unrar`, `unar`, `7z`,
  or `7zz`.
- A torrent client only if the page posts magnets. If it posts plain links,
  choose `direct` below and skip this.

On macOS, `brew install python unar` covers the missing pieces. The stock
`/usr/bin/python3` is a stub until you install Command Line Tools. On Windows,
WinRAR ships `UnRAR.exe`; rename a copy to `unrar.exe` and add that folder to
your `PATH`, or install `unar`.

**Step 2. Clone the repo and copy two files.**

Windows (PowerShell):

```powershell
git clone https://github.com/soficis/quartermaster.git
cd quartermaster
Copy-Item feed\config.example.json feed\config.json
Copy-Item feeds\generic-example.json feeds\local-myaddon.json
```

macOS and Linux (any shell):

```sh
git clone https://github.com/soficis/quartermaster.git
cd quartermaster
cp feed/config.example.json feed/config.json
cp feeds/generic-example.json feeds/local-myaddon.json
```

**Step 3. Fill in your preset.**

Open `feeds/local-myaddon.json` in any text editor. Fill in these settings:

```json
{
  "name": "my addon",
  "thread_url": "https://example.com/threads/1-releases.html",
  "release_prefix": "Demo",
  "archive_pattern": "Demo",
  "downloader": { "method": "direct" },
  "flavors": {
    "retail": {
      "label": "Retail",
      "addon": "DemoAddon",
      "wow": "_retail_",
      "rank": 0
    }
  }
}
```

- `thread_url` - the page you wrote down in step 1.
- `release_prefix` - the token release filenames start with.
- `archive_pattern` - text that picks the right files. Use `""` for all files.
- `flavors` - one entry per client you play. `addon` is the folder name.
  `wow` is `_retail_`, `_classic_`, `_classic_era_`, and so on.
- `downloader` - use `direct` for plain links. Use `watchdir` for magnets.

Now open `feed/config.json` and set the preset to the file you just made:

```json
{
  "preset": "local-myaddon"
}
```

Keep the other lines in `config.json`. Your values there override the preset.
See [Feeds and presets](#feeds-and-presets) for the full field list.

**Step 4. Sign in once. Skip this if the page is public.**

Run this on a computer with a desktop. Sign in in the window it opens, then
press Enter in the console.

Windows (PowerShell):

```powershell
.\tools\Export-Cookies.ps1
Copy-Item $env:TEMP\addon-cookies.json feed\state\cookies.json
python feed\reader.py import-cookies
```

macOS and Linux (PowerShell 7):

```powershell
./tools/Export-Cookies.ps1
Copy-Item $env:TEMP/addon-cookies.json feed/state/cookies.json
python3 feed/reader.py import-cookies
```

Delete the exported JSON when the import succeeds.

**Step 5. Publish once to prove the feed works.**

Windows (PowerShell):

```powershell
python feed\worker.py check --force
python feed\worker.py status
```

macOS and Linux (PowerShell 7):

```powershell
python3 feed/worker.py check --force
python3 feed/worker.py status
```

The check should exit 0. `status` should show your flavors and
`"needs_login": false`. If it shows `needs_login`, redo step 4.

**Step 6. Point the client at the publish folder.**

Windows (PowerShell):

```powershell
.\client\Setup.ps1 -ManifestBase "C:\path\to\quartermaster\feed\state\publish"
```

macOS and Linux (PowerShell 7):

```powershell
./client/Setup.ps1 -ManifestBase "$PWD/feed/state/publish"
```

Setup asks for the publish location if you leave it out. It detects WoW at the
usual locations and asks which ones to add. It warns you when a folder is not a
WoW layout. It never writes into a folder you did not confirm.

Run the dry run next. It changes nothing:

Windows (PowerShell):

```powershell
.\client\sync.ps1 -WhatIf
```

macOS and Linux (PowerShell 7):

```powershell
./client/sync.ps1 -WhatIf
```

You want one line per WoW install. If you get none, the client thinks the roots
are idle; [Fix common problems](#fix-common-problems) explains the fix.

**Step 7. Schedule both halves.**

Windows (PowerShell):

```powershell
.\client\Install-Schedule.ps1
.\feed\Install-FeedSchedule.ps1
```

macOS and Linux (PowerShell 7):

```powershell
./client/Install-Schedule.ps1
./feed/Install-FeedSchedule.ps1
```

The client runs at 07:30 and 19:30. The feed runs daily around 05:30, with a
spread. Add `-WhatIf` to see what either installer would do.

That is it. Later, `worker.py status` may say `needs_login`. Re-export cookies
then. Everything else runs on its own.

## How it works

Two halves meet at one file. That file is `manifest.json`.

| Part | What it does | Where |
|---|---|---|
| feed builder | reads the page, fetches and unpacks releases, writes the manifest | `feed/` |
| preset | says which page to read and what to call each build | `feeds/` |
| publish folder | holds addon folders, zips, and `manifest.json` | `feed/state/publish/` |
| sync client | reads the manifest, verifies SHA-256, backs up, and installs | `client/` |
| schedulers | run both halves on Windows, macOS, or Linux | `feed/`, `client/` |

```
  the page your preset points at
  (maybe behind a login)
          |
          v
  feed/worker.py -> magnets or links -> downloader -> unrar/7z
          |
          v
  <publish>/<flavor>/
      <addon folder>/          ready to copy
      <addon>-<version>.zip    what the client fetches
      manifest.json            name, version, sha256, size
          |
          v
  client/sync.ps1, twice a day
      verify SHA-256 -> back up -> replace -> stamp .synced-version
```

Because the halves share only `manifest.json`, they can run on one PC or on
different machines. That is the whole design.

## What quartermaster does and does not do

quartermaster is a downloader and an installer. It fetches archives, unpacks
them, and puts them where the game reads them.

- It ships no addon. Only code that moves folders around.
- It publishes to a folder you own. No server, no upload, no mirror.
- It trusts the page. Whatever the feed calls current, the client installs.
- It checks the bytes. The client verifies SHA-256 before replacement. It
  refuses entries with no checksum or names that point outside the target
  folder.
- It is not affiliated with Blizzard or with any addon author.

## Pick a shape

You already have shape A if you followed Path 2. Change shape only when your
setup needs it.

| Shape | Feed runs on | PCs read it via | Choose it when |
|---|---|---|---|
| **A. One PC** | your gaming PC | a local folder | you have one machine |
| **B. Home box** | an always-on computer | SMB/NFS share | several PCs, one always-on box |
| **C. Static HTTP** | any box with Python | `http://host:8000` | you want no file shares |
| **D. Portal** | an existing home portal | the portal's page | you already run one |
| **E. Direct links only** | your gaming PC | a local folder | you run no torrent client |
| **F. Custom downloader** | any of the above | any of the above | your client has a CLI |

A is the fast path. Everything lives under `feed/state/`.

B is the durable one. A small box that never sleeps does the fetching, and each
PC just reads a folder. The systemd units and `install.sh` assume this shape.

C replaces the share with `python3 -m http.server`, Caddy, nginx, or a NAS web
share. The client accepts a URL anywhere it accepts a path.

D only makes sense if you already run a Python portal. `examples/homelab/` is
one person's deployment. Ignore it otherwise.

E and F are about how magnets become files. `watchdir` drops a `.magnet` into a
folder your client watches. `direct` fetches plain links and skips magnets.
`command` runs your client's CLI with `{magnet}` substituted.

What varies across shapes: where the feed runs, how archives arrive, how PCs
read the feed, which flavors exist, which installs get touched, and when the
jobs run. Nothing else. If you are changing more than that, the shape is wrong.

## Requirements

| Need | Why |
|---|---|
| Python 3.7+ | feed builder. Standard library only; no pip. |
| Chromium, Chrome, or Edge | the feed reads the page through a real browser |
| an extractor on `PATH` | `unrar` or `unar` for RAR; `7z`, `7zz`, or `unar` for `.7z` |
| PowerShell 5.1+ | client. 5.1 on Windows, 7+ elsewhere. |
| a torrent client | only for magnet links; not needed with `direct` |
| a session on the page | if the source has a login wall; one sign-in lasts months |

Windows: WinRAR ships `UnRAR.exe`, not `unrar.exe`. Copy it to `unrar.exe` in
the WinRAR folder, add that folder to `PATH`, and reopen the shell. Or install
`unar`. Plain `.zip` releases need no extractor.

macOS: no PowerShell ships by default. Install PowerShell 7 from
<https://aka.ms/ps7>; the client, cookie exporter, and both scheduler installers
need it. Install Python and an extractor with `brew install python unar`, or get
Python from python.org. Chrome and Chromium are found in `/Applications`. You
set no browser path.

Linux: the client needs PowerShell 7. On a Debian-like host you control as
root, `feed/install.sh` installs the rest and registers a daily timer.

## Feeds and presets

You did this in Path 2, step 3. This is the reference.

The engine knows nothing about what it fetches. Every source name lives in a
small JSON file called a preset:

```
feeds/generic-example.json       one addon, HTTP links, no login
feeds/five-flavors-example.json  one feed, five clients, five addon folders
```

Both are shapes, not sources. Their `thread_url` is blank. A preset says where
to look and what to call the result:

```json
{
  "name": "my feed",
  "thread_url": "https://example.com/threads/1-releases.html",
  "release_prefix": "Demo",
  "archive_pattern": "Demo",
  "flavors": {
    "retail": { "label": "Retail", "addon": "DemoAddon", "wow": "_retail_", "rank": 0 }
  },
  "downloader": { "method": "direct" }
}
```

- `thread_url` - the page to read. The reader drives a browser you signed in
  to, so it sees what you see.
- `release_prefix` - the token a release filename starts with.
- `archive_pattern` - a regex for which archives matter. `""` means all.
- `flavors` - one entry per client: addon folder, WoW folder, label, sort order.
- `downloader`, `check_hours`, and other keys supply defaults. A non-empty
  value in `config.json` overrides them.

Pick a preset with `"preset": "<name>"` in `feed/config.json`, or with the
`ADDONSYNC_PRESET` environment variable. Addon names, site names, and release
naming appear only in these files, never in Python.

To make your own, copy `feeds/generic-example.json`, fill in the settings,
save it as `feeds/local-<name>.json`, and set `preset` to `local-<name>`. No
Python needed. Git ignores files starting with `local-`.

## Signing in to the page

The feed needs a browser session, not a cookie string you paste by hand.

**Export, recommended.** Run this on a PC with a desktop:

```powershell
.\tools\Export-Cookies.ps1
```

Sign in in the window it opens, not in your usual browser. Wait for the page to
render, then press Enter. The script writes `addon-cookies.json` to your temp
folder. Copy that file to the feed host and import it by path:

```sh
python3 /opt/addon-sync/reader.py import-cookies /path/to/addon-cookies.json
```

Import refuses to report success until the page shows attachments. Delete the
JSON afterwards. Path 2, step 4 shows the same-PC commands.

**VNC, Linux feed host.** `reader.py login` starts Chromium plus x11vnc and
prints an SSH forward. Sign in inside that browser, then check with
`reader.py read`. Without Xvfb the browser is headless, so use export instead.

Sessions last months. When `status` says `needs_login`, run the export again.

## Flavors

A flavor is one WoW client generation: `retail`, `classic`, `era`, and so on.
Your preset declares which ones exist, what to name each addon folder, and
which WoW subfolder to use.

```powershell
python feed/worker.py status      # lists the flavors in your preset
```

The five-flavor example spells out all five in
`feeds/five-flavors-example.json`. Your feed can declare any names you like.

A root can hold flavor subfolders. That is the Battle.net layout. Or a root can
hold one direct client, with `Interface/AddOns` at its top. Then `DirectFlavor`
picks its flavor, also settable with `Setup.ps1 -DirectFlavor`. It defaults to
`classic`, except that a root named `_retail_`, `_classic_`, and so on names its
own flavor. The client collapses duplicate roots. It expands roots starting
with `~/` or holding `%VARS%` on every platform.

A root counts as active when its WTF data changed within 30 days. A past sync
also marks it. Idle roots stay untouched.

## Verify it worked

Run these four. Each is read-only.

```powershell
python feed/worker.py status      # last_ok, last_error, needs_login, preset, flavors
python feed/reader.py status      # browser up? page open?
.\client\sync.ps1 -WhatIf         # what would be installed, touches nothing
Get-ScheduledTask -TaskName AddonSync, AddonFeed   # Windows
```

On macOS and Linux, run `./client/sync.ps1 -WhatIf` instead of the third line.

On macOS, both halves are launchd agents:
`launchctl list | Select-String com.addonsync`. The feed label is
`com.addonsync.feed`. On Linux, the feed timer is
`systemctl --user list-timers AddonFeed.timer`, or
`systemctl list-timers addonsync-feed.timer` after `install.sh`. The client is
`systemctl --user list-timers AddonSync.timer`.

Healthy looks like this:

- `last_ok` within `check_hours`, and `last_error` empty
- `needs_login` false
- `flavors` listing the builds you expect
- `thread_open` true in the reader status
- one `-WhatIf` line per target install, with no download pending

## Fix common problems

- **`needs_login: true`** - the session expired. Re-export cookies.
- **The same bundle downloads twice** - magnets dedupe by info hash. Two
  different hashes are two different uploads.
- **The version looks odd** - the `.toc` carries `major.minor`. The real build
  number comes from the archive name. Without one there, the `.toc` must carry
  it, or the check fails instead of publishing `0`.
- **`-Times` or `-At` refused** - the schedulers take `HH:mm` only. They stop
  before registering a job that cannot fire.
- **`-Uninstall -WhatIf`** - reports what it would remove and removes nothing,
  on every platform.
- **Empty watch folder** - the torrent client never delivered. The check fails
  after `timeout_min` instead of hanging.
- **`no rar extractor`** - install `unrar` or `unar` on the PATH of the account
  that runs the feed. On macOS, a launchd job gets a small PATH; the installer
  writes your install-time PATH into the plist, so install the extractor first.
- **`python at ... does not run`** - on macOS that is the Command Line Tools
  stub, on Windows the Microsoft Store alias. Install Python 3, or pass
  `-Python <exe>`, then run the installer again.
- **Setup warns `not a WoW root`** - you passed a folder with no
  `Interface/AddOns` inside it or in a `_flavor_` folder. Pass the parent folder
  or the direct client folder instead.
- **The client says up to date but the addon is old** - it compares versions
  first. Check `.synced-version` in the installed folder.
- **Nothing installed on a fresh PC** - no WTF data means no active root. Run
  once with `-Include <flavor>`.

## Schedule the feed and client

Path 2, step 7 registers both halves. This is the reference.

The client installer is the same on every platform: Task Scheduler on Windows,
a launchd agent on macOS, a systemd user timer on Linux. `-Times` takes one or
more `HH:mm` values, as an array or comma-separated (`-Times 06:30,18:30`).
The installers refuse anything else before registering a job. `-WhatIf` shows
what it would do. `-Uninstall` removes it.

The feed installer works on every platform too:

```powershell
.\feed\Install-FeedSchedule.ps1
```

It runs `worker.py check --scheduled`. Windows and Linux pick a fresh random
spread on every run. launchd has no per-run delay, so macOS picks one random
offset at install time. `-At "06:00"`, `-RandomMinutes 0`, `-Uninstall`, and
`-WhatIf` work everywhere.

On a Linux box you control as root, `feed/install.sh` is the system-wide
alternative. Use one or the other. A user timer fires only while you are logged
in unless you enable lingering.

## Set up an always-on feed host

Shape B moves the feed off your gaming PC:

```sh
sudo ./feed/install.sh
```

The script installs Chromium and friends, copies the worker and `feeds/` to
`/opt/addon-sync`, writes `/etc/addon-sync.json` from the example when missing,
and enables a daily timer at a random time. Put your `local-` preset in
`/opt/addon-sync/feeds/` before you run it, or copy it there afterwards.

Share the result over SMB, or serve it:

```sh
cd /var/lib/addon-sync/publish && python3 -m http.server 8000
```

Caddy, if you prefer something that survives a reboot:

```
:8000 {
    root * /var/lib/addon-sync/publish
    file_server browse
}
```

Then on each gaming PC:

```powershell
.\client\Setup.ps1 -ManifestBase http://your-host:8000
```

## Change the setup with an agent

Install is one prompt. An agent is also the easiest way to rewire later,
because the tradeoffs live here.

Example follow-ups:

- "Add my second WoW install at `D:\Games\WoW` to the client and keep retail only."
- "Switch the feed to `direct`; I do not run a torrent client."
- "Move the feed to my Debian box and serve it over HTTP on port 8000."
- "Make the client run at 06:00 and 18:00 instead of 07:30 and 19:30."
- "Add the anniversary flavor to the feed and to every client."
- "Write me a preset for a different addon's release thread."
- "The status says `needs_login`. Walk me through re-exporting cookies."

Tell the agent never to overwrite addon folders outside `-WhatIf`, install into
roots you did not name, commit a `*.config.json`, `*cookie*.json`, or `*.pw`,
print cookie values, or `git push`.

## Run the tests

A clean checkout runs green with no setup:

```powershell
python -m unittest discover -s tests -v     # 58 tests, feed side
.\client\tests\smoke.ps1                     # end-to-end client, 55 checks
```

The client smoke test builds a throwaway WoW root and publish folder, then runs
the real `sync.ps1`. It covers dry run, install, up-to-date, upgrade with
backup, a bad hash the client must reject, a manifest entry with no checksum
it must refuse, and two roots sharing one flavor that get separate backups.
It also dry-runs both scheduler installers, checks that the installers refuse
bad `-Times` and `-At` values, that `-Uninstall -WhatIf` removes nothing, and
that a comma-separated `-Roots` becomes two roots. It checks install detection:
a parent with retail/classic/era, two direct clients, a missing root, a
duplicate root, a `~/` root, and a `DirectFlavor` from config.

The feed tests cover version ordering, release-name and URL fallbacks, magnet
handling, opaque download names, archive extraction including Homebrew's `7zz`
and `unar`, import failures, publish and manifest, and every shipped preset.

`.github/workflows/ci.yml` runs the tests on Python 3.7, 3.8 and 3.12, runs the
client smoke test under Windows PowerShell 5.1 and PowerShell 7, dry-runs both
scheduler installers, parses every PowerShell file, and syntax-checks
`install.sh`.

The same suite passes on macOS 12.7 (PowerShell 7.4) and Ubuntu 26.04
(PowerShell 7.7). We registered a real scheduler, ran it where it made sense,
and removed it on each platform: a Scheduled Task on Windows, launchd agents
for both halves on macOS, and systemd user timers for both halves on Linux. On macOS the feed
agent ran a real check against a local page: headless Chrome read it, the zip
downloaded and extracted, and the addon plus manifest landed in `publish`. The
cookie exporter also ran there; it found Chrome in `/Applications`, used the
default profile path containing a space, and wrote the cookie JSON. We proved
install detection on all three with real installs into a parent holding
retail/classic/era, a direct client, a client named for its flavor, and a `~/`
root. On macOS and Linux, interactive Setup detected a WoW install at the
platform default and wrote it to the config after confirmation.

## Repo map

```
feeds/
  five-flavors-example.json  one feed, five clients (shape, no source)
  generic-example.json       neutral single-addon template
feed/
  worker.py                  page -> download -> extract -> publish + manifest
  reader.py                  Chromium reader (DevTools) + cookie import + VNC login
  config.example.json        pick a preset; everything else has a default
  manual-feed.example.json   fallback links when the browser cannot read
  install.sh                 Linux service setup (packages, /opt/addon-sync, timer)
  Install-FeedSchedule.ps1   feed scheduler: Task Scheduler / launchd / systemd user timer
  addonsync-feed.service     oneshot check (overridable via ADDONSYNC_* env)
  addonsync-feed.timer       once daily at a random time
client/
  Setup.ps1                  first-run wizard: config file + scheduler
  sync.ps1                   manifest -> verify -> backup -> replace
  Install-Schedule.ps1       Task Scheduler / launchd / systemd user timer
  sync.config.example.json
  tests/smoke.ps1            end-to-end test of sync.ps1 and both schedulers
tools/
  Export-Cookies.ps1         open the page, sign in, dump cookies to JSON
tests/                       unittest suite for worker.py, reader.py, presets
examples/homelab/            one person's portal deployment (optional reading)
archive/                     retired scripts and notes (local only, gitignored)
AGENTS.md                    setup contract for coding agents
NOTICE                       what it does; trademarks; warranty
RELEASING.md                 release checklist: tag it, attach nothing
.github/workflows/ci.yml     CI: unittest, client smoke, PS parse, shell check
.github/ISSUE_TEMPLATE/      content change request form
LICENSE                      GPLv3
```

## Legal

GPLv3. See `LICENSE` and `NOTICE`.

The examples in `feeds/` use placeholder folder names and point at no live
page. Trademarks belong to their owners. Nothing here is affiliated with or
endorsed by them. If an example is yours and you would like it changed, open an
[issue](https://github.com/soficis/quartermaster/issues/new?template=content-request.yml).

<details>
<summary>Notes for AI agents (opt in to be a nerd)</summary>

**The contract is `manifest.json`.**

```json
{
  "flavors": {
    "retail": {
      "addon": "DemoAddon",
      "version": "11.2.0",
      "zip": "DemoAddon-11.2.0.zip",
      "sha256": "...",
      "size": 123456,
      "source": "archive"
    }
  }
}
```

Consumers ignore unknown keys. The client reads `<base>/manifest.json` and then
`<base>/<flavor>/<zip>`, where `<base>` is a path or an http(s) URL.

**Division of labour.** `reader.py` returns raw magnets and attachment links.
All classification, naming, and version logic lives in `worker.py`. That keeps
the browser side dumb and avoids a circular import.

**`reader.py` is standard library only**, including a raw-socket WebSocket
client for the DevTools protocol. It resolves its state and config from
`ADDONSYNC_STATE_DIR` and `ADDONSYNC_CONFIG`, else from files next to itself.

**Download methods** are `watchdir` (write `.magnet`, wait for a file), `direct`
(HTTP only), and `command` (run argv with `{magnet}` substituted). See
`DEFAULTS` in `worker.py`.

**One lock per run.** `fcntl` is absent on Windows, so there the scheduler does
the serializing. `check --scheduled` bypasses the due-gate; `check_hours` only
feeds the status display.

**Presets are data, not code.** If a task seems to need an addon name or a site
name in Python, it probably belongs in `feeds/<name>.json`.

**Never commit secrets.** Git ignores `*.config.json`, `*cookie*.json`, `*.pw`,
and `feed/state/`. Never print cookie values.

**Verify before claiming success:**

```powershell
python -m unittest discover -s tests
.\client\tests\smoke.ps1
python feed/worker.py status
python feed/reader.py status
```

**Portal code stays in `examples/homelab/`.** `feed/` must never import it.

</details>

## License

GPLv3. See `LICENSE`. New files carry the standard header notice; the text is
at the end of `LICENSE`.
