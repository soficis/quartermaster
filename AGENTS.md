# AGENTS.md

Setup contract for coding agents, for **quartermaster**. Read this before
touching anything, then read `README.md` for the shapes and tradeoffs.

## What this is

A feed builder watches a page and publishes addon builds as a folder of zips
plus `manifest.json`. A sync client on each gaming PC reads that manifest,
verifies SHA-256, backs up, and replaces the addon. Transfer and local
install only; this repo never redistributes the addon. GPLv3.

The engine is generic. Where to look, what the addon folders are called, and
how releases are named all live in `feeds/<name>.json`, selected by `preset`
in `feed/config.json` or by `ADDONSYNC_PRESET`. Never hardcode an addon or
site name in Python; if a task needs one, it belongs in a preset.

Your job in a setup session: get one working shape (README "Pick a
shape"), verify it, and leave the user with a scheduled update.

## Ask the user, in this order

1. **Which shape?** Default to **A. One PC** unless they mention an
   always-on server, NAS, or Pi. Read the shape table to them, one line each.
2. **Where is WoW?** Take the root(s): a folder with `Interface/AddOns`,
   or a parent holding `_retail_`, `_classic_`, etc. Do not install into a
   root the user did not name: `client/Setup.ps1` detects installs at the
   per-OS guesses and asks which to add. It also expands `~/` and `%VARS%`
   and accepts `-DirectFlavor` for a standalone client.
3. **Which page?** The URL of the page that links the release archives. A
   login wall is fine: the reader drives a browser the user signs in to. Ask
   too for the addon folder name and the token the archive names start with.
4. **Which flavors?** One per WoW client the source publishes for. Most
   users want `retail`; some want `classic` or `era` as well.

Do not ask anything else. Everything below is decidable from the repo.

## Write the preset

The repo ships shapes, not sources: `feeds/generic-example.json` (one
addon) and `feeds/five-flavors-example.json` (one addon per client). The
user's page goes in a `local-` file, which is gitignored, so it never
reaches the public repo:

```powershell
Copy-Item feeds\generic-example.json feeds\local-<name>.json
```

Fill in `name`, `thread_url`, `release_prefix`, `archive_pattern`, and one
`flavors` entry per client. Start from the five-flavor example instead when
the source publishes a separate archive for each client. Then set
`"preset": "local-<name>"` in `feed/config.json` and check it:

```powershell
python feed/worker.py status          # preset and flavors match what you wrote
python feed/worker.py check --force   # exit 0, flavors populated
```

If the page needs a login, do the cookie step below before the check.

## Non-negotiables

- Never commit or print `*.config.json`, `*cookie*.json`, `*.pw`, cookies,
  tokens, or SSH secrets. They are gitignored for a reason.
- Never `git push`, force-push, or rewrite history.
- Run `client/sync.ps1 -WhatIf` and show the user the result before
  the first real sync.
- Do not install into a WoW root the user did not name.
- Do not delete or move `feed/state/publish` — it is the last good release.
- The page login is the user's. Never ask them to paste a cookie into
  chat; point them at `tools/Export-Cookies.ps1` or `reader.py login`.
- Never attach an archive to a release, and never point anyone at a feed URL
  you host. The repo is the release. See `RELEASING.md`.

## Check prerequisites first

```powershell
python --version                     # need 3.7+
python -c "import shutil; print(shutil.which('unrar') or shutil.which('unar'))"
$PSVersionTable.PSVersion            # need 5.1+ for the client
```

Browser: Chromium, Chrome, or Edge must be installed. The worker finds it;
you do not set a path. On Windows, if `unrar` is missing but WinRAR is
installed, copy `UnRAR.exe` to `unrar.exe` in the WinRAR folder and add
that folder to the user `PATH`, then reopen the shell. On macOS, `brew
install python unar` (the stock `/usr/bin/python3` is a stub until the
Command Line Tools are installed). `.zip` releases need nothing; other
formats need `7z`/`7zz` or `unar` on `PATH`. On Debian, `install.sh`
handles all of it.

## Shape A: one PC (default)

Feed and client on the same machine, local folder, no server.

```powershell
# from the repo root
Copy-Item feed/config.example.json feed/config.json
```

Then edit `feed/config.json` only if needed:
- `preset` picks the feed definition in `feeds/` and supplies `thread_url`,
  the addon folder names, and the release naming. Set it to the
  `local-<name>` file you wrote above.
- `thread_url` left empty means "use the preset's". Set it to override.
- `downloader.method`: `watchdir` (default), `direct`, or `command`.
- Leave `watch_dir` / `completed_dir` / `publish_dir` empty to use
  `feed/state/magnets`, `feed/state/incoming`, `feed/state/publish`.

Ask which downloader they have. If they run qBittorrent/Transmission/
Deluge, have them add `feed/state/magnets` as a watched folder and set
`completed_dir` to its finished folder. If they run nothing, set
`"method": "direct"` and accept that magnet-only releases are skipped.

Then the human step. Run this on a PC with a desktop:

```powershell
.\tools\Export-Cookies.ps1        # opens the page; sign in; press Enter
Copy-Item $env:TEMP\addon-cookies.json feed/state/cookies.json
python feed/reader.py import-cookies   # ok:true and attachments > 0
Remove-Item $env:TEMP\addon-cookies.json, feed/state/cookies.json
```

The exporter takes its URL from `feeds/<preset>.json`. Pass `-Preset <name>`
or `-ThreadUrl <url>` if the feed uses something else.

`import-cookies` exiting 0 with `"ok": true` and `"attachments"` greater
than 0 means the session works. If it says the thread shows no
attachments, the sign-in did not land: run the exporter again, sign in in
the window it opens (not your usual browser), wait for the thread to
render, then press Enter.

Then publish and install:

```powershell
python feed/worker.py check --force     # exit 0, flavors populated
.\client\Setup.ps1 -ManifestBase <repo>\feed\state\publish `
  -Roots "<wow root>" -InstallSchedule -Yes
```

`-Yes` makes Setup non-interactive and also registers the scheduler. If
the user wants different times, use `-Times "06:00","18:00"`. `-NoSchedule`
writes the config and registers nothing.

Schedule the feed half too:

```powershell
.\feed\Install-FeedSchedule.ps1    # daily 05:30 + jitter; Task Scheduler,
                                   # launchd, or a systemd user timer
```

`-At "06:00"`, `-RandomMinutes 0`, `-Uninstall`, and `-WhatIf` work. On
Windows the task is `AddonFeed`; on macOS the launchd label is
`com.addonsync.feed`; on Linux the user timer is `AddonFeed.timer`. On a
root-run Debian host, `install.sh` already registered
`addonsync-feed.timer`; use one or the other.

## Shape B: always-on Debian-like host

```sh
sudo ./feed/install.sh
```

It installs packages, copies the worker and `feeds/` to `/opt/addon-sync`,
writes `/etc/addon-sync.json` when missing, and enables `addonsync-feed.timer`
(daily, random time). Then:

1. Edit `/etc/addon-sync.json` for `preset` (the `local-<name>` file you
   wrote) and `downloader`, if they do not want the `watchdir` defaults
   under `/var/lib/addon-sync`. `install.sh` copies `feeds/`, so the
   `local-` preset has to exist before you run it, or copy it into
   `/opt/addon-sync/feeds/` afterwards.
2. Cookies: copy the exported JSON to the host and run
   `/usr/bin/python3 /opt/addon-sync/reader.py import-cookies`, then delete it.
3. `sudo /usr/bin/python3 /opt/addon-sync/worker.py check --force`
4. Share `/var/lib/addon-sync/publish` over SMB, or serve it:

```sh
cd /var/lib/addon-sync/publish && python3 -m http.server 8000
```

5. On each gaming PC, run `client/Setup.ps1 -ManifestBase http://host:8000`.

Note `install.sh` starts a Chromium warm-up and prints next steps. If
Chromium is not in the distro repos, install it from the vendor and rerun.

## Shape C/D/E/F

- **Static HTTP**: same feed, publish served by `http.server`, Caddy,
  nginx, or a NAS web share. Client gets a URL.
- **Portal**: only if the user already runs one; point them at
  `examples/homelab/` and do not import it from `feed/`.
- **Direct**: `"method": "direct"`. Verify with a check; it is normal for
  `magnets` to be reported as skipped.
- **Command**: `"command": ["transmission-remote", "-a", "{magnet}"]`.
  Test the binary exists before saving the config.

## Verify, then report

```powershell
python feed/worker.py status      # JSON: ok, preset, last_ok, last_error, needs_login, flavors
python feed/reader.py status      # JSON: browser, thread_open, port
.\client\sync.ps1 -WhatIf         # summary only, no writes
```

Healthy: `needs_login` false, `last_ok` within `check_hours`, expected
flavors under `flavors`, `thread_open` true, and a `-WhatIf` line per
target install. Then check the schedulers exist:

```powershell
Get-ScheduledTask -TaskName AddonSync, AddonFeed          # Windows
launchctl list | Select-String com.addonsync              # macOS (client + feed)
systemctl --user list-timers AddonSync.timer, AddonFeed.timer  # Linux user
systemctl list-timers addonsync-feed.timer                 # Linux after install.sh
```

If you changed code, run before reporting:
`python -m unittest discover -s tests` and `client/tests/smoke.ps1`.

Report to the user: shape chosen, publish path or URL, flavors, next run
time, and the one thing that needs them (re-export cookies when
`needs_login` appears, months from now).

## Triage

| Symptom | Cause | Fix |
|---|---|---|
| `needs_login: true` | session expired | re-export cookies, `import-cookies` |
| `no rar extractor` | `unrar`/`unar` not on the service account's PATH | install it, restart the service |
| check times out, empty watch folder | torrent client not watching `watch_dir` | fix the client's watch folder, or set `direct` |
| nothing installed on a fresh PC | no WTF data, root looks idle | run once with `-Include <flavor>` |
| `ManifestBase is not set` | no config file and no flag | copy the example or pass the flag |
| empty `flavors` | no successful check yet | `worker.py check --force`, read `last_error` |
| `feed preset not found` | bad `preset` value, or `feeds/` was not deployed | set `preset` to a file in `feeds/`, or pass `ADDONSYNC_PRESET` with a full path |

When a check fails, `last_error` holds the reason and the previous publish
is untouched by design. Fix forward; do not delete state to "reset".
