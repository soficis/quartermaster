# quartermaster

Your addons stay current. You play.

quartermaster watches a page where addon builds get published, fetches each
release with a downloader you already run, and installs it into the World of
Warcraft folders you name. It runs on a schedule, while you sleep.

Transfer and local install only. The page stays the source, and nothing here
redistributes anything.

---

## Point your agent at it

This repo is meant to be installed by a coding agent. Paste this into
Claude Code, Codex, Cursor, or whatever you use:

> Clone `https://github.com/soficis/quartermaster` and set it up for me.
> Read `AGENTS.md` first, then read `README.md` "Pick a shape" and ask me
> which shape I want. Do the rest yourself and verify each step.

`AGENTS.md` is the setup contract: prerequisites, exact commands, what to
check, and the rules. The agent will ask you four things:

1. Which shape (one PC, always-on host, shared folder or HTTP).
2. Where your WoW folders are.
3. Which flavors you play.
4. Which page to read — any page that links addon archives, including one
   behind a login.

It writes `feeds/local-<name>.json` for that page. Presets named `local-*`
are gitignored, so the source you use is yours and does not ship with the
repo.

One step always needs a human: **signing in to the page**. If the source sits
behind a login wall, the feed needs a real session, and only you can create
one. `tools/Export-Cookies.ps1` opens a browser on the page, waits for you to
sign in, and hands the session to the feed. Two minutes, once every few
months.

## What this is

Not one program. A small set of tools that can be wired together several
ways. Two halves meet at one file: `manifest.json`.

| Piece | What it does | Where |
|---|---|---|
| feed builder | reads the page, fetches releases, publishes addon folders + zips + manifest | `feed/` |
| sync client | reads the manifest, verifies SHA-256, backs up, replaces | `client/` |
| cookie export | hands your browser session to the feed | `tools/` |
| installers | feed + client schedulers on Windows (Task Scheduler), macOS (launchd) and Linux (systemd) | `feed/install.sh`, `feed/Install-FeedSchedule.ps1`, `client/Install-Schedule.ps1` |
| optional portal | a web page listing builds, for an existing home portal | `examples/homelab/` |

```
  the page a feed points at
  (maybe a login wall)
          |
          v
  feed/worker.py  ->  magnets + attachment links  ->  your downloader  ->  unrar
          |
          v
  <publish>/<flavor>/
      <addon folder>/            ready to copy
      <addon>-<version>.zip      what the client downloads
      manifest.json              name, version, sha256, size
          |
          v
  client/sync.ps1 on each PC, twice a day
      verify SHA-256 -> back up -> replace -> stamp .synced-version
```

Because the halves share only `manifest.json`, they can run on the same PC or
on different machines. That is the whole design.

## Scope

quartermaster is a downloader and an installer. It fetches archives, unpacks
them, and puts them where the game reads them.

- **It ships no addon.** Only code that moves folders around.
- **It publishes to a folder you own.** No server, no upload, no mirror.
- **It trusts the page.** Whatever the feed says is current is what gets
  installed.
- **It checks the bytes.** SHA-256 is verified before anything is replaced,
  and an entry with no checksum, or with an addon or zip name that points
  outside the target folder, is refused rather than installed.
- **It is not affiliated** with Blizzard or with any addon author.

## Pick a shape

| Shape | Feed runs on | PCs read it via | Good when |
|---|---|---|---|
| **A. One PC** | your gaming PC | a local folder | you have one machine |
| **B. Home box** | an always-on computer | SMB/NFS share | several PCs, one always-on box |
| **C. Static HTTP** | any box with Python | `http://host:8000` | you want no file shares |
| **D. Portal** | an existing home portal | the portal's page | you already run one |
| **E. Direct links only** | your gaming PC | a local folder | you run no torrent client |
| **F. Custom downloader** | any of the above | any of the above | your client has a CLI |

**A** is the fast path: both halves on one machine, everything under
`feed/state/`, no server, no share. Start here unless something pushes you
out.

**B** is the durable one. A small box that never sleeps does the fetching,
and every PC just reads a folder. This is the shape the systemd units and
`feed/install.sh` assume.

**C** replaces the share with `python3 -m http.server` (or Caddy, nginx, a
NAS web share). The client accepts a URL anywhere it accepts a path.

**D** only makes sense if you already run a Python portal. `examples/homelab/`
is one person's deployment, kept out of `feed/` on purpose. Ignore it
otherwise.

**E** and **F** are about how magnets become files. `watchdir` drops a
`.magnet` into a folder your client watches; `direct` skips magnets and
fetches plain links only; `command` runs your client, with `{magnet}`
substituted.

### What varies

- **Where the feed runs** - same PC, home server, VPS.
- **How archives arrive** - a watched folder, a CLI, or plain HTTP.
- **How PCs read the feed** - local path, SMB/NFS, http(s).
- **Which flavors** - one, or as many as the source publishes.
- **Which installs get touched** - activity detection, `-Include`,
  `-Exclude`, `DirectFlavor`.
- **When it runs** - a `check_hours` gate, or your OS scheduler.

Nothing else. If you find yourself changing more than that, the shape is
probably wrong for your setup.

## Requirements

| Need | Why |
|---|---|
| Python 3.7+ | feed builder. Stdlib only, no pip. |
| Chromium, Chrome, or Edge | the feed reads the page through a real browser |
| a RAR extractor on `PATH` | `unrar` or `unar`; `.7z` also takes `7z`, `7zz` or `unar`; `install.sh` installs `unrar` |
| PowerShell 5.1+ | client. 5.1 on Windows, 7+ elsewhere. |
| a torrent client | only for magnet links; not needed with `direct` |
| a session on the page | if the source has a login wall; one sign-in lasts months |

On Windows, WinRAR ships `UnRAR.exe`, not `unrar.exe`. Copy it to
`unrar.exe` in the WinRAR folder, add that folder to `PATH`, and reopen the
shell. Or install `unar`. `.zip` releases need nothing; other formats need
7-Zip (`7z`).

macOS has no PowerShell by default: the client, the cookie exporter, and both
scheduler installers need PowerShell 7 from <https://aka.ms/ps7>. The
`.tar.gz` release unpacks under your home folder and needs no admin rights.
Python 3 and a RAR extractor are not part of a stock macOS either:
`brew install python unar` (or Python from python.org) covers the feed. The
system `/usr/bin/python3` is only a stub until the Command Line Tools are
installed, and the feed scheduler refuses it rather than registering a job
that cannot run. Chrome or Chromium is found in `/Applications`; no path
setting is needed.

## Feeds and presets

The engine knows nothing about what it is fetching. Every name lives in a
small JSON file:

```
feeds/generic-example.json       one addon, HTTP links, no login
feeds/five-flavors-example.json  one feed, five clients, five addon folders
```

Both are shapes, not sources: `thread_url` is blank in each. A preset says
where to look and what to call the result:

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

- `thread_url` - the page to read. It may sit behind a login: the reader
  drives a browser you signed in to, so the page is read the way you see it.
- `release_prefix` - the token a release filename starts with.
- `archive_pattern` - a regex for which archives matter, or `""` for all.
- `flavors` - addon folder name, WoW folder, display label, sort order.
- anything else (`downloader`, `check_hours`, ...) supplies a default; a
  non-empty value in `config.json` overrides it.

Pick one with `"preset": "<name>"` in `feed/config.json`, or with the
`ADDONSYNC_PRESET` environment variable. Addon names, site names, and
release naming appear only in these files, never in code.

**Make your own:** copy `feeds/generic-example.json`, fill in those four
blocks, save it as `feeds/local-<name>.json`, and set `preset` to
`local-<name>`. No Python needed. Files starting with `local-` are
gitignored, so the source you point at stays yours and never ships with the
repo.

## Manual setup: one PC

You need Python 3, Chromium (or Chrome), and a signed-in session. The client
needs PowerShell: 5.1 works on Windows, 7+ elsewhere.

```powershell
# 1. Configure the feed. Pick a preset; everything else has a default
#    under feed/state/.
Copy-Item feed/config.example.json feed/config.json

# 2. Pick how magnets become files. Easiest: point the torrent client
#    you already run at two folders.
#    watch_dir     = <state>/magnets     (client watches this)
#    completed_dir = <state>/incoming    (client finishes here)
#    Leave both empty to use those defaults.
#    No torrent client? Use "direct": http links download, magnets skip.
#    Power user? Use "command" with e.g. ["transmission-remote", "-a", "{magnet}"].

# 3. Give the feed browser your session. The script opens a browser on the
#    page and waits: sign in there, then press Enter in the console.
.\tools\Export-Cookies.ps1          # writes %TEMP%\addon-cookies.json
Copy-Item $env:TEMP\addon-cookies.json feed/state/cookies.json
python feed/reader.py import-cookies
python feed/worker.py check --force # exit 0, flavors populated

# 4. Set up the client. Point it at the publish folder.
.\client\Setup.ps1 -ManifestBase C:\path\to\feed\state\publish
```

`Setup.ps1` detects WoW installs at the usual per-OS locations, asks which
ones to use, warns when a folder is not a WoW layout, writes
`client/sync.config.json`, and offers to register the scheduler. A root can
be a parent holding `_retail_`, `_classic_`, `_classic_era_`, ... or a direct
client (`Interface/AddOns` at its top); `~/` and `%VARS%` in a root are
expanded. Delete the cookies JSON when it is done.

Fully non-interactive, when every value is known:

```powershell
.\client\Setup.ps1 -ManifestBase C:\path\to\feed\state\publish `
  -Roots "C:\Program Files (x86)\World of Warcraft" -InstallSchedule -Yes
```

`-NoSchedule` writes the config and registers nothing, for when you manage
the scheduler yourself.

The client's scheduler installer is the same on every platform: Task
Scheduler on Windows, a launchd agent on macOS, a systemd user timer on
Linux. `-Times` takes one or more `HH:mm` values, as an array or
comma-separated (`-Times 06:30,18:30`); anything else is refused before
anything is registered. `-WhatIf` shows what it would do, and `-Uninstall`
removes it.

The feed half needs a schedule too. The same installer works on every
platform:

```powershell
.\feed\Install-FeedSchedule.ps1   # daily 05:30 + up to 3h spread
```

It runs `worker.py check --scheduled`. On Windows and Linux the spread is
picked on every run (Task Scheduler `RandomDelay`, systemd
`RandomizedDelaySec`); launchd has no per-run random delay, so on macOS the
installer picks one random offset now and keeps it. `-At "06:00"`,
`-RandomMinutes 0`, `-Uninstall`, and `-WhatIf` also work everywhere.

On a Linux box you control as root, `feed/install.sh` is still the
system-wide alternative; use one or the other. A user timer only fires while
you are logged in unless lingering is on.

## Manual setup: always-on feed host

```sh
sudo ./feed/install.sh
```

It installs Chromium and friends, copies the worker and `feeds/` to
`/opt/addon-sync`, writes `/etc/addon-sync.json` from the example when
missing, and enables a daily timer at a random time.

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

Then on each PC:

```powershell
.\client\Setup.ps1 -ManifestBase http://your-host:8000
```

## Signing in to the page

The feed needs a browser session on the page, not a cookie string you paste
by hand.

**Export (recommended).** Run this on a PC with a desktop:

```powershell
.\tools\Export-Cookies.ps1            # opens the page, waits, dumps JSON
```

Sign in in the window it opens (not your usual browser), wait for the page to
render, press Enter. It writes `%TEMP%\addon-cookies.json`. Copy that to the
feed host, then:

```sh
python3 /opt/addon-sync/reader.py import-cookies
```

It refuses to report success until the page actually shows attachments, so a
silent failure is hard to miss. Delete the file afterwards.

**VNC.** On a Linux feed host, `reader.py login` starts Chromium plus x11vnc
and prints an SSH forward. Sign in inside that browser, then check with
`reader.py read`. Without Xvfb the browser is headless, so export is the only
path.

Sessions last months. When `status` says `needs_login`, run the export again.

## Flavors

A flavor is one WoW client generation: `retail`, `classic`, `era`, and so
on. The feed's preset declares which ones exist, what the addon folder is
called in each, and which WoW subfolder it belongs in.

```powershell
python feed/worker.py status      # "labels" lists the flavors this preset has
```

The five-flavor example spells out all five in
`feeds/five-flavors-example.json`. Your own feed can declare any names you
like.

A root holds flavor subfolders. That is the Battle.net layout. Or a root
holds one direct client (`Interface/AddOns` at its top). Then `DirectFlavor`
picks which flavor it gets (also settable with `Setup.ps1 -DirectFlavor`);
it defaults to `classic`, except that a root folder named `_retail_`,
`_classic_`, ... names its own flavor. Duplicate roots are collapsed, and
roots beginning `~/` or containing `%VARS%` are expanded, on every platform.
A root counts as **active** when its WTF data changed within 30 days. A past
sync also marks it: the sync stamps every install it touches. Idle roots
stay untouched.

## Verify it worked

Run these four. Each is read-only.

```powershell
python feed/worker.py status      # last_ok, last_error, needs_login, preset, flavors
python feed/reader.py status      # browser up? page open?
.\client\sync.ps1 -WhatIf         # what would be installed, touches nothing
Get-ScheduledTask -TaskName AddonSync, AddonFeed   # Windows
```

On macOS both halves are launchd agents:
`launchctl list | Select-String com.addonsync` (the feed label is
`com.addonsync.feed`). On Linux the feed timer is
`systemctl --user list-timers AddonFeed.timer` (or
`systemctl list-timers addonsync-feed.timer` after `feed/install.sh`), and
the client is `systemctl --user list-timers AddonSync.timer`.

Healthy looks like:

- `last_ok` within `check_hours`, `last_error` empty
- `needs_login` false
- `flavors` listing the builds you expect
- `thread_open` true in the reader status
- a `-WhatIf` line per target install, no download pending

## Troubleshooting

- **`needs_login: true`** - the session expired. Re-export cookies.
- **The same bundle downloads twice** - magnets dedupe by info hash; two
  different hashes are two different uploads.
- **The version looks odd** - the `.toc` carries `major.minor`; the real
  build number comes from the archive name. When the name carries no
  version, the `.toc` must, or the check fails instead of publishing `0`.
- **`-Times` or `-At` refused** - the scheduler installers take `HH:mm`
  only and stop before registering anything, rather than writing a task or
  timer that cannot fire.
- **`-Uninstall -WhatIf`** - reports what it would remove and removes
  nothing, on every platform.
- **Empty watch folder** - the torrent client never delivered. The check
  fails loudly after `timeout_min` rather than hanging.
- **`no rar extractor`** - install `unrar` or `unar` on the PATH of the
  account that runs the feed. On macOS, a launchd job gets a minimal PATH;
  the installer writes your install-time PATH into the plist, so install the
  extractor before running it.
- **`python at ... does not run`** - on macOS that is the Command Line Tools
  stub, on Windows the Microsoft Store alias. Install Python 3 (or pass
  `-Python <exe>`), then run the installer again.
- **The client says up to date but the addon is old** - it compares versions
  first; check `.synced-version` in the installed folder.
- **Nothing installed on a fresh PC** - no WTF data means no active root. Run
  once with `-Include <flavor>`.

## Working with an agent

Install is one prompt. An agent is also the easiest way to change the wiring
later, because the tradeoffs are written down here.

Example follow-ups:

- "Add my second WoW install at `D:\Games\WoW` to the client and keep
  retail only."
- "Switch the feed to `direct`; I do not run a torrent client."
- "Move the feed to my Debian box and serve it over HTTP on port 8000."
- "Make the client run at 06:00 and 18:00 instead of 07:30 and 19:30."
- "Add the anniversary flavor to the feed and to every client."
- "Write me a preset for a different addon's release thread."
- "The status says `needs_login`. Walk me through re-exporting cookies."

What the agent should never do without asking: overwrite addon folders
outside `-WhatIf`, force-install into roots you did not name,
commit a `*.config.json`, `*cookie*.json`, or `*.pw`, print cookie values,
or `git push`.

## Tests

A clean checkout runs green with no setup:

```powershell
python -m unittest discover -s tests -v     # 58 tests, feed side
.\client\tests\smoke.ps1                     # end-to-end client, 55 checks
```

The client smoke test builds a throwaway WoW root and publish folder, then
runs the real `sync.ps1`: dry run, install, up-to-date, upgrade with
backup, a corrupted hash that must be rejected, a manifest entry with no
checksum that must be refused, and two roots sharing one flavor that must
get separate backups. It also dry-runs both scheduler installers, checks
that bad `-Times`/`-At` values are refused, that `-Uninstall -WhatIf`
removes nothing, and that a comma-separated `-Roots` becomes two roots. It
also checks install detection: a parent with retail/classic/era, two direct
clients (one named for its flavor), a missing root, a duplicate root, a
`~/` root and a `DirectFlavor` from config.

The feed tests cover version ordering, release-name and URL fallbacks,
magnet handling, opaque download names, archive extraction (including
Homebrew's `7zz` and `unar` for `.7z`), import failures, publish + manifest,
and every preset that ships.

`.github/workflows/ci.yml` runs the tests on Python 3.7, 3.8 and 3.12, runs
the client smoke test under both Windows PowerShell 5.1 and PowerShell 7,
dry-runs both scheduler installers, parses every PowerShell file, and
syntax-checks `install.sh`.

The same suite also passes on macOS 12.7 (PowerShell 7.4) and Ubuntu
26.04 (PowerShell 7.7). A real scheduler was registered, run where it made
sense, and removed on each: a Scheduled Task on Windows, launchd agents
for both halves on macOS, and systemd user timers for both halves on
Linux. On macOS the feed agent ran a real check against a local page:
headless Chrome read it, the zip downloaded and extracted, and the addon
plus manifest landed in `publish`. The cookie exporter was also run there:
it found Chrome in `/Applications`, used the default profile path
containing a space, and wrote the cookie JSON.

## Repo map

```
feeds/
  five-flavors-example.json  one feed, five clients (shape, no source)
  generic-example.json      neutral single-addon template
feed/
  worker.py                 page -> download -> extract -> publish + manifest
  reader.py                 Chromium reader (DevTools) + cookie import + VNC login
  config.example.json       pick a preset; everything else has a default
  manual-feed.example.json  fallback links for when the browser cannot read
  install.sh                Linux service setup (packages, /opt/addon-sync, timer)
  Install-FeedSchedule.ps1  feed scheduler: Task Scheduler / launchd / systemd user timer
  addonsync-feed.service    oneshot check (overridable via ADDONSYNC_* env)
  addonsync-feed.timer      once daily at a random time
client/
  Setup.ps1                 first-run wizard: config file + scheduler
  sync.ps1                  manifest -> verify -> backup -> replace
  Install-Schedule.ps1      Task Scheduler / launchd / systemd user timer
  sync.config.example.json
  tests/smoke.ps1           end-to-end test of sync.ps1 and both schedulers
tools/
  Export-Cookies.ps1        open the page, sign in, dump cookies to JSON
tests/                      unittest suite for worker.py, reader.py, presets
examples/homelab/           one person's portal deployment (optional reading)
archive/                    retired scripts and notes (local only, gitignored)
AGENTS.md                   setup contract for coding agents
NOTICE                      what it does; trademarks; warranty
RELEASING.md                release checklist: tag it, attach nothing
.github/workflows/ci.yml    CI: unittest, client smoke, PS parse, shell check
.github/ISSUE_TEMPLATE/     content change request form
LICENSE                     GPLv3
```

## Legal

GPLv3. See `LICENSE` and `NOTICE`.

The examples in `feeds/` name a page and some folder names. Trademarks belong
to their owners, and nothing here is affiliated with or endorsed by them. If an
example is yours and you would like it changed, open an
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

Consumers ignore unknown keys. The client reads `<base>/manifest.json` and
then `<base>/<flavor>/<zip>`, where `<base>` is a path or an http(s) URL.

**Division of labour.** `reader.py` returns raw magnets and attachment
links. All classification, naming, and version logic lives in `worker.py`.
That keeps the browser side dumb and avoids a circular import.

**`reader.py` is stdlib only**, including a raw-socket WebSocket client for
the DevTools protocol. It resolves its state and config from
`ADDONSYNC_STATE_DIR` and `ADDONSYNC_CONFIG`, else from files next to itself.

**Download methods** are `watchdir` (write `.magnet`, wait for a file),
`direct` (HTTP only), and `command` (run argv with `{magnet}` substituted).
See `DEFAULTS` in `worker.py`.

**One lock per run.** `fcntl` is absent on Windows, so there the scheduler
does the serializing. `check --scheduled` bypasses the due-gate;
`check_hours` only feeds the status display.

**Presets are data, not code.** If a task seems to need an addon name or a
site name in Python, it probably belongs in `feeds/<name>.json`.

**Never commit secrets.** `*.config.json`, `*cookie*.json`, `*.pw`, and
`feed/state/` are gitignored. Never print cookie values.

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

GPLv3. See `LICENSE`. New files carry the standard header notice; the text
is at the end of `LICENSE`.
