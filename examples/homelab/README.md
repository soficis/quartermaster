# Example: portal-hosted setup

One person's deployment, kept as a working example. The main repo needs
none of this. Copy ideas from it only if you run a similar portal.

## What it is

- `plugins/addons.py` + `plugins/__init__.py`: a self-contained page,
  status JSON, zip downloads and check-now button for a Python portal.
  It imports the feed worker for state and publish paths.
- `patch_portal_plugins.py`: one-time migration from an inline portal
  patch to the plugin loader. Backs up `portal.py` first.
- `cellproxy-addons.service` / `.timer`: oneshot check once daily at a
  random time, wired to this portal's paths.
- `deploy.ps1` (+ `deploy.config.example.json`): pushes `feed/` to the
  host over SSH, installs packages, migrates the portal, warms the
  browser, re-imports. Needs `deploy.config.json` (gitignored).

## Notes

- The worker reads the same `feed/config.example.json` keys. State lands
  next to the deployed worker unless `ADDONSYNC_STATE_DIR` says otherwise;
  a fresh state loses `seen_magnets` and re-downloads the bundle once.
- `plugins/addons.py` implements its own page/status/file/trigger code.
  `feed/worker.py` only exposes `status_data()` for scripts.
