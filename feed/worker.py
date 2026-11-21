#!/usr/bin/env python3
"""Addon feed watcher, downloader, extractor and publisher.

Runs on any machine with Python 3 and Chromium. One job: every check
interval, read the feed page through the local browser
(reader.py), download new addon release archives with your own downloader,
extract each flavor, and publish a clean addon tree + zip per flavor.

Output is a plain directory: <publish_dir>/<flavor>/ with the addon folder,
a versioned zip, and a top-level manifest.json. Serve that directory any
way you like (local folder, file share, static HTTP). The sync client
reads the manifest and installs what is new.

The only feed input besides the browser is a manually written feed file
(fallback when the browser cannot read the thread).

Design rules:
  * never overwrite a good publish with a broken extract
  * one lock, one run at a time
  * every state write is atomic
  * a failed check keeps the last good state and records the error
  * Forever is handled first
"""

try:
    import fcntl
except ImportError:  # Windows: no POSIX locks; the scheduler fires one run
    fcntl = None  # at a time, so this is only best-effort protection
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# All local state lives under one directory. Override it and the config path
# with the environment when the files live elsewhere (a service install
# points them at /var/lib/addon-sync and /etc/addon-sync.json). Defaults stay next
# to this script so a plain checkout works with zero setup.
STATE_DIR = (os.environ.get("ADDONSYNC_STATE_DIR") or
             os.path.join(BASE_DIR, "state"))
CONF = (os.environ.get("ADDONSYNC_CONFIG") or
        os.path.join(BASE_DIR, "config.json"))
STATE = os.path.join(STATE_DIR, "state.json")
LOCK = os.path.join(STATE_DIR, "lock")
STAGE = os.path.join(STATE_DIR, "stage")
LOG = os.path.join(STATE_DIR, "worker.log")
DEF_PUBLISH = os.path.join(STATE_DIR, "publish")
DEF_INCOMING = os.path.join(STATE_DIR, "incoming")

# ---------------------------------------------------------------- feed preset
#
# What the feed *is* -- which page, which addon folders, how release files are
# named -- lives in feeds/<name>.json, not here. Change the preset and the
# same worker watches a different page for a different addon. config.json
# picks one with "preset"; ADDONSYNC_PRESET overrides it.

PRESET_DIRS = (os.path.join(BASE_DIR, "feeds"),
               os.path.join(os.path.dirname(BASE_DIR), "feeds"))
DEF_PRESET = "generic-example"


def read_json(path, default):
    # utf-8-sig so a config saved by PowerShell (BOM) still parses.
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def preset_path(name):
    """Resolve a preset name or path to a file, or None."""
    if not name:
        return None
    if os.path.isfile(name):
        return os.path.abspath(name)
    for folder in PRESET_DIRS:
        for cand in (name, name + ".json"):
            path = os.path.join(folder, cand)
            if os.path.isfile(path):
                return path
    return None


_RAW_CONFIG = read_json(CONF, {})
if not isinstance(_RAW_CONFIG, dict):
    _RAW_CONFIG = {}
PRESET_NAME = (os.environ.get("ADDONSYNC_PRESET")
               or _RAW_CONFIG.get("preset")
               or DEF_PRESET)
PRESET_FILE = preset_path(PRESET_NAME)
if not PRESET_FILE:
    raise SystemExit("feed preset not found: %s (looked in %s)"
                     % (PRESET_NAME, " and ".join(PRESET_DIRS)))
PRESET = read_json(PRESET_FILE, {})
if not isinstance(PRESET, dict) or not PRESET.get("flavors"):
    raise SystemExit("feed preset has no flavors: %s" % PRESET_FILE)

# flavor -> how it is labelled, which addon folder it owns, which WoW dir it
# belongs in on Windows. The addon folder is also read from the archive; this
# is the expected name and the tie-breaker when an archive has several.
FLAVORS = PRESET["flavors"]
ORDER = sorted(FLAVORS, key=lambda f: FLAVORS[f].get("rank", 99))

DEFAULTS = {
    "preset": PRESET_NAME,
    "check_hours": 24,
    "manifest_file": None,       # manual fallback feed; default <state>/manual-feed.json
    "publish_dir": None,         # default <state>/publish
    "downloads_dir": None,       # incoming archives; default <state>/incoming
    "keep_zips": 2,
    "request_timeout": 40,
    "downloader": {
        # How magnet links become files. One of:
        #   watchdir: write each magnet to <watch_dir> as <infohash>.magnet
        #     and wait for the archive to land in <completed_dir>. Point any
        #     torrent client at those two folders; no integration needed.
        #   direct:   skip magnets, download http(s) links only.
        #   command:  run <command> with {magnet} replaced, then wait like
        #     watchdir (for `transmission-remote -a {magnet}` and friends).
        "method": "watchdir",
        "watch_dir": None,       # default <state>/magnets
        "completed_dir": None,   # default = downloads_dir
        "timeout_min": 120,
        "command": [],
    },
}

# A preset may carry ordinary settings too (thread_url, downloader, ...).
_PRESET_SPECIAL = ("name", "_comment", "flavors", "release_prefix",
                   "archive_pattern")
for _key, _value in PRESET.items():
    if _key in _PRESET_SPECIAL:
        continue
    if isinstance(_value, dict) and isinstance(DEFAULTS.get(_key), dict):
        _merged = dict(DEFAULTS[_key])
        _merged.update(_value)
        DEFAULTS[_key] = _merged
    else:
        DEFAULTS[_key] = _value


def publish_dir(cfg):
    return cfg.get("publish_dir") or DEF_PUBLISH


def incoming_dir(cfg):
    return cfg.get("downloads_dir") or DEF_INCOMING


def manual_feed_file(cfg):
    return (cfg.get("manual_feed_file") or cfg.get("manifest_file")
            or os.path.join(STATE_DIR, "manual-feed.json"))


# ---------------------------------------------------------------- utilities

def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def _is_unset(value):
    return value is None or value == "" or value == []


def merge_settings(base, overrides):
    """Apply config.json on top of defaults + preset.

    Empty values ("", [], null) mean "not set" and keep what is underneath, so
    a verbatim copy of config.example.json does not blank the preset's
    thread_url. Dict values merge one level deep, like the preset merge, so a
    config "downloader" block changes only the keys it sets.
    """
    out = dict(base)
    if not isinstance(overrides, dict):
        return out
    for key, value in overrides.items():
        if _is_unset(value):
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update({k: v for k, v in value.items() if not _is_unset(v)})
            out[key] = merged
        else:
            out[key] = value
    return out


def config():
    return merge_settings(DEFAULTS, read_json(CONF, {}))


def state():
    st = read_json(STATE, {})
    st.setdefault("flavors", {})
    st.setdefault("seen_magnets", [])
    st.setdefault("last_check", 0)
    st.setdefault("last_ok", 0)
    st.setdefault("last_error", "")
    st.setdefault("needs_login", False)
    for legacy in ("needs_cookies", "last_manifest", "trigger"):
        st.pop(legacy, None)
    return st


def save_state(st):
    st["updated"] = int(time.time())
    write_json(STATE, st)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def version_tuple(text):
    out = []
    for part in re.findall(r"\d+", text or ""):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def newer(a, b):
    """True when version a is newer than b."""
    if not b:
        return bool(a)
    ta, tb = version_tuple(a), version_tuple(b)
    width = max(len(ta), len(tb))
    ta += (0,) * (width - len(ta))
    tb += (0,) * (width - len(tb))
    return ta > tb


def human(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.1f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
        n /= 1024.0


# ---------------------------------------------------------------- release names

_FLAVOR_WORDS = "|".join(sorted((re.escape(f) for f in FLAVORS),
                                key=len, reverse=True))
_VER = r"\d+(?:[\s._-]\d+){1,4}"
_RELEASE_PREFIX = re.escape(PRESET.get("release_prefix") or "")
_PATTERNS = (
    re.compile(r"%s[\s._-]*Release[\s._-]*(?P<flavor>%s)[\s._-]+(?P<ver>%s)"
               % (_RELEASE_PREFIX, _FLAVOR_WORDS, _VER), re.I),
    re.compile(r"%s[\s._-]*Release[\s._-]+(?P<ver>%s)[\s._-]+(?P<flavor>%s)"
               % (_RELEASE_PREFIX, _VER, _FLAVOR_WORDS), re.I),
)


def clean_name(text):
    text = urllib.parse.unquote(text or "")
    text = re.sub(r"\.(rar|zip|7z|torrent)$", "", text, flags=re.I)
    return re.sub(r"[\s._-]+", " ", text).strip()


def parse_release(text):
    """Return (flavor, version) from a filename, or (None, None)."""
    hay = clean_name(text)
    for pat in _PATTERNS:
        m = pat.search(hay)
        if m:
            flavor = m.group("flavor").lower()
            ver = ".".join(re.findall(r"\d+", m.group("ver")))
            if flavor in FLAVORS and ver:
                return flavor, ver
    return None, None


def parse_magnet_name(url):
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        for key in ("dn", "name"):
            if qs.get(key):
                return qs[key][0]
    except ValueError:
        pass
    return ""


def info_hash(url):
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        for xt in qs.get("xt", []):
            if xt.lower().startswith("urn:btih:"):
                return xt.split(":", 2)[2].lower()
    except ValueError:
        pass
    return hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------- feed

def fetch_browser(cfg):
    """Read the thread through the local Chromium (reader); classify links."""
    import reader  # same directory; raises ImportError when missing
    raw = reader.read_feed(cfg)
    items, bundles = [], []
    for m in raw.get("magnets", []):
        url = m.get("url") or ""
        name = m.get("name") or parse_magnet_name(url)
        flavor, ver = parse_release(name)
        if flavor:
            items.append({"flavor": flavor, "version": ver, "url": url,
                          "name": name, "kind": "magnet"})
        else:
            bundles.append({"flavor": None, "version": "", "url": url,
                            "name": name or "magnet", "kind": "magnet"})
    for a in raw.get("attachments", []):
        url = a.get("url") or ""
        name = a.get("name") or ""
        flavor, ver = parse_release(name)
        if not flavor:
            flavor, ver = parse_release(url)
        if flavor:
            items.append({"flavor": flavor, "version": ver, "url": url,
                          "name": name, "kind": "http"})
        else:
            bundles.append({"flavor": None, "version": "", "url": url,
                            "name": name or "attachment", "kind": "http"})
    fingerprint = hashlib.sha256(
        ("\n".join(sorted(info_hash(m.get("url") or "")
                           for m in raw.get("magnets", []))) + "\n" +
         "\n".join(sorted(a.get("url") or ""
                          for a in raw.get("attachments", [])))
         ).encode("utf-8")).hexdigest()
    return {"items": items, "bundles": bundles, "fingerprint": fingerprint,
            "magnets": [m.get("url") or "" for m in raw.get("magnets", [])],
            "attachments": len(raw.get("attachments", [])),
            "source": "browser"}


def fetch_manifest(cfg):
    path = manual_feed_file(cfg)
    data = read_json(path, {})
    items, bundles = [], []
    for flavor, entry in (data.get("flavors") or {}).items():
        flavor = flavor.lower()
        if flavor not in FLAVORS:
            continue
        url = entry.get("url") or entry.get("magnet") or ""
        if not url:
            continue
        items.append({"flavor": flavor, "version": entry.get("version") or "",
                      "url": url, "name": entry.get("name") or flavor,
                      "kind": "magnet" if url.startswith("magnet:") else "http"})
    for entry in data.get("bundles") or []:
        url = entry.get("url") or ""
        if url:
            bundles.append({"flavor": None, "version": entry.get("version") or "",
                            "url": url, "name": entry.get("name") or "bundle",
                            "kind": "magnet" if url.startswith("magnet:") else "http"})
    fingerprint = hashlib.sha256(
        json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
    return {"items": items, "bundles": bundles, "fingerprint": fingerprint,
            "magnets": [b["url"] for b in bundles], "source": "manifest"}


def fetch_feed(cfg):
    """Browser first; the manual manifest file only when that fails."""
    errors = []
    try:
        feed = fetch_browser(cfg)
        if feed["items"] or feed["bundles"]:
            return feed
        errors.append("browser: no release links found (logged out?)")
    except Exception as exc:
        errors.append("browser: %s" % exc)
    feed = fetch_manifest(cfg)
    if feed["items"] or feed["bundles"]:
        feed["browser_error"] = "; ".join(errors)
        return feed
    errors.append("manifest: empty or missing")
    raise RuntimeError("; ".join(errors))


# ---------------------------------------------------------------- download

ARCHIVE_RE = re.compile(r"\.(rar|zip|7z)$", re.I)
_ARCHIVE_NAME = re.compile(PRESET.get("archive_pattern") or "", re.I) \
    if PRESET.get("archive_pattern") else None


def archive_matches(name):
    """True when a filename is an archive this feed cares about."""
    name = name or ""
    if not ARCHIVE_RE.search(name):
        return False
    return _ARCHIVE_NAME is None or bool(_ARCHIVE_NAME.search(name))


def downloader_cfg(cfg):
    d = dict(cfg.get("downloader") or {})
    method = (d.get("method") or "watchdir").lower()
    incoming = incoming_dir(cfg)
    watch = d.get("watch_dir") or os.path.join(STATE_DIR, "magnets")
    completed = d.get("completed_dir") or incoming
    try:
        timeout = float(d.get("timeout_min") or 120)
    except (TypeError, ValueError):
        timeout = 120
    return {"method": method, "watch_dir": watch, "completed_dir": completed,
            "timeout_min": timeout, "command": d.get("command") or []}


def snapshot_archives(directory):
    try:
        return set(n for n in os.listdir(directory)
                   if ARCHIVE_RE.search(n))
    except OSError:
        return set()


def wait_for_new_archives(known, directory, timeout_min):
    """Poll until archives appear that were not in `known`; return paths."""
    deadline = time.time() + max(timeout_min, 1) * 60
    while time.time() < deadline:
        try:
            names = os.listdir(directory)
        except OSError:
            names = []
        fresh = sorted(n for n in names
                       if ARCHIVE_RE.search(n) and n not in known)
        if fresh:
            return [os.path.join(directory, n) for n in fresh]
        time.sleep(15)
    raise RuntimeError("no archive appeared in %s after %s min "
                       "(check the torrent client)" % (directory, timeout_min))


def enqueue_magnet(url, cfg):
    """Turn one magnet into local archive paths, per the downloader config."""
    dl = downloader_cfg(cfg)
    method = dl["method"]
    if method == "direct":
        log("skipping magnet (downloader=direct): %s" % url[:90])
        return []
    os.makedirs(dl["watch_dir"], exist_ok=True)
    os.makedirs(dl["completed_dir"], exist_ok=True)
    known = snapshot_archives(dl["completed_dir"])
    if method == "command":
        argv = [(a.replace("{magnet}", url) if a != "{magnet}" else url)
                for a in dl["command"]]
        if not argv:
            raise RuntimeError("downloader.command is empty")
        log("fetching magnet via: %s" % " ".join(argv)[:120])
        subprocess.run(argv, check=True, capture_output=True, timeout=300)
        return wait_for_new_archives(known, dl["completed_dir"],
                                     dl["timeout_min"])
    if method != "watchdir":
        raise RuntimeError("unknown downloader.method: %s "
                           "(want watchdir, direct or command)" % method)
    name = info_hash(url) + ".magnet"
    with open(os.path.join(dl["watch_dir"], name), "w",
              encoding="utf-8") as fh:
        fh.write(url + "\n")
    log("magnet queued in %s; waiting for the archive in %s"
        % (dl["watch_dir"], dl["completed_dir"]))
    return wait_for_new_archives(known, dl["completed_dir"],
                                 dl["timeout_min"])


def browser_cookies():
    """Session cookies from the browser profile (for attachment fetches)."""
    try:
        import reader  # type: ignore
        return reader.browser_cookies() or []
    except Exception:
        return []


def page_host(cfg):
    """Host of the feed page; picks the cookies an attachment fetch needs."""
    try:
        return (urllib.parse.urlparse(cfg.get("thread_url") or "").hostname
                or "").lower()
    except ValueError:
        return ""


def cookie_matches(domain, host):
    """True when a cookie domain belongs to the page host."""
    d = (domain or "").lstrip(".").lower()
    return bool(d and host and (host == d or host.endswith("." + d)))


def _download_name(url, response):
    """A filename with an archive extension for a download.

    Forum attachments often hide behind opaque URLs (/attachments/12345/),
    which would otherwise save extensionless and be skipped later as
    unidentifiable. Prefer the server's Content-Disposition filename, then
    the URL tail, then sniff the Content-Type; never return bare.
    """
    cands = []
    disp = response.headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disp, re.I)
    if m:
        cands.append(urllib.parse.unquote(m.group(1).strip().strip('"')))
    cands.append(os.path.basename(urllib.parse.urlparse(url).path))
    for raw in cands:
        name = re.sub(r"[^A-Za-z0-9._ -]+", "_",
                      urllib.parse.unquote(raw or ""))
        if name and archive_matches(name):
            return name
    ctype = response.headers.get_content_type()
    ext = {"application/zip": ".zip",
           "application/x-rar-compressed": ".rar",
           "application/vnd.rar": ".rar",
           "application/x-7z-compressed": ".7z"}.get(ctype, ".rar")
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_",
                  urllib.parse.unquote(cands[-1] or "download")) or "download"
    return base + ext


def http_download(url, cfg, dest_dir):
    """Direct download; uses the browser session when the host needs it."""
    host = page_host(cfg)
    cookies = [c for c in browser_cookies()
               if cookie_matches(c.get("domain"), host)]
    headers = {"User-Agent": "Mozilla/5.0"}
    if cfg.get("thread_url"):
        headers["Referer"] = cfg["thread_url"]
    if cookies:
        headers["Cookie"] = "; ".join(
            "%s=%s" % (c["name"], c["value"]) for c in cookies)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req,
                                    timeout=cfg.get("request_timeout", 40)) as r:
            dest = os.path.join(dest_dir, _download_name(url, r))
            tmp = dest + ".part"
            with open(tmp, "wb") as fh:
                shutil.copyfileobj(r, fh)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("HTTP %d for %s" % (exc.code, url))
    os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------- extract/publish

def _extract_with(exe, path, dest):
    """Run unrar/7z/unar with the flags that tool understands."""
    name = os.path.basename(exe).lower()
    if name.startswith("unar"):
        # unar handles rar and 7z, and uses -o for the output directory.
        cmd = [exe, "-f", "-o", dest, path]
    elif name.startswith("7z"):
        cmd = [exe, "x", "-y", "-o" + dest, path]
    else:
        # unrar takes the archive then the destination directory.
        cmd = [exe, "x", "-o+", "-y", path, dest + os.sep]
    subprocess.run(cmd, check=True, capture_output=True)


def extract_archive(path, dest):
    os.makedirs(dest, exist_ok=True)
    low = path.lower()
    if low.endswith(".rar"):
        exe = shutil.which("unrar") or shutil.which("unar")
        if not exe:
            raise RuntimeError("no rar extractor (install unrar or unar)")
        _extract_with(exe, path, dest)
    elif low.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            zf.extractall(dest)
    else:
        # Homebrew's sevenzip package installs 7zz, not 7z; unar covers 7z
        # on a Mac that has no 7-Zip at all.
        exe = (shutil.which("7z") or shutil.which("7zz")
               or shutil.which("7za") or shutil.which("unar"))
        if not exe:
            raise RuntimeError("no extractor for %s (install 7z or unar)" % path)
        _extract_with(exe, path, dest)


def find_addon_root(dest, expected=""):
    candidates = []
    for root, _dirs, files in os.walk(dest):
        for f in files:
            if f.lower().endswith(".toc"):
                candidates.append(root)
                break
    if not candidates:
        return None
    # A flat archive (toc at the root) yields dest itself as a candidate;
    # callers name it with addon_name_for(), never by dest's temp name.
    if expected:
        for c in candidates:
            if os.path.basename(c).lower() == expected.lower():
                return c
    known = [m.get("addon", "").lower() for m in FLAVORS.values()]
    known = [n for n in known if n]
    for c in candidates:
        base = os.path.basename(c).lower()
        if any(base.startswith(n) for n in known):
            return c
    return candidates[0]


def _toc_stems(folder):
    try:
        return sorted(os.path.splitext(f)[0] for f in os.listdir(folder)
                      if f.lower().endswith(".toc"))
    except OSError:
        return []


def addon_name_for(root, dest, expected=""):
    """The addon folder name to publish `root` under.

    Normally the folder's own name. When the archive is flat (the .toc sits
    at the archive root), `root` is the extraction directory, whose name is a
    random temp name; use the expected addon name when its .toc is there, else
    the shortest .toc stem (DemoAddon.toc beats DemoAddon_Mainline.toc).
    """
    same = (os.path.normcase(os.path.abspath(root))
            == os.path.normcase(os.path.abspath(dest)))
    if not same:
        return os.path.basename(root)
    stems = _toc_stems(root)
    if expected and any(s.lower() == expected.lower() for s in stems):
        return expected
    if expected and any(s.lower().startswith(expected.lower()) for s in stems):
        return expected
    if stems:
        return min(stems, key=len)
    return expected or os.path.basename(root)


def toc_version(folder):
    """Dotted digits from the first `## Version:` line in the folder's tocs."""
    for stem in _toc_stems(folder):
        try:
            with open(os.path.join(folder, stem + ".toc"),
                      encoding="utf-8-sig", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"\s*##\s*Version\s*:\s*(.+)", line, re.I)
                    if m:
                        ver = ".".join(re.findall(r"\d+", m.group(1)))
                        if ver:
                            return ver
        except OSError:
            continue
    return ""


def make_zip(folder, zip_path):
    tmp = zip_path + ".tmp"
    parent = os.path.dirname(folder)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, parent)
                zf.write(full, rel)
    os.replace(tmp, zip_path)


def prune(directory, pattern, keep):
    try:
        names = [n for n in os.listdir(directory) if re.search(pattern, n)]
    except OSError:
        return
    names.sort(key=lambda n: os.path.getmtime(os.path.join(directory, n)),
               reverse=True)
    for name in names[keep:]:
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass


def _sweep_stale_stages(dest, older_than=3600):
    # A crash between staging and cleanup leaves .new-*/.old-* in the served
    # folder. Only old ones go: on Windows there is no lock, and a young one
    # may belong to a run still in progress.
    now = time.time()
    try:
        names = os.listdir(dest)
    except OSError:
        return
    for name in names:
        if not (name.startswith(".new-") or name.startswith(".old-")):
            continue
        path = os.path.join(dest, name)
        try:
            if now - os.path.getmtime(path) > older_than:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def publish(flavor, addon_dir, version, archive, source, cfg,
            addon_name=None):
    meta = FLAVORS[flavor]
    pub_root = publish_dir(cfg)
    dest = os.path.join(pub_root, flavor)
    os.makedirs(dest, exist_ok=True)
    _sweep_stale_stages(dest)
    addon_name = addon_name or os.path.basename(addon_dir)

    stage = os.path.join(dest, ".new-%s" % uuid.uuid4().hex[:8])
    os.makedirs(stage, exist_ok=True)
    target = os.path.join(stage, addon_name)
    shutil.copytree(addon_dir, target, symlinks=True)
    with open(os.path.join(target, ".synced-version"), "w",
              encoding="utf-8") as fh:
        fh.write(version + "\n")

    final = os.path.join(dest, addon_name)
    old = os.path.join(dest, ".old-%s" % uuid.uuid4().hex[:8])
    if os.path.exists(final):
        os.replace(final, old)
    os.replace(target, final)
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)

    zip_name = "%s-%s.zip" % (addon_name, version)
    zip_path = os.path.join(dest, zip_name)
    make_zip(final, zip_path)
    digest = sha256_file(zip_path)
    size = os.path.getsize(zip_path)

    # keep the last N zips per flavor
    prune(dest, r"^%s-\d.*\.zip$" % re.escape(addon_name),
          int(cfg.get("keep_zips", 2)))

    return {
        "flavor": flavor,
        "label": meta["label"],
        "addon": addon_name,
        "wow_dir": meta["wow"],
        "version": version,
        "published": int(time.time()),
        "zip": zip_name,
        "sha256": digest,
        "size": size,
        "source": source,
        "archive": os.path.basename(archive) if archive else "",
    }


def import_archive(path, cfg, st, force=False):
    flavor, ver = parse_release(os.path.basename(path))
    if not flavor:
        # generic bundle member: extract and identify from the folder/toc
        tmp = tempfile.mkdtemp(prefix="addon-sync-id-", dir=STAGE)
        try:
            extract_archive(path, tmp)
            root = find_addon_root(tmp)
            if not root:
                return []
            addon = addon_name_for(root, tmp).lower()
            for f, meta in FLAVORS.items():
                if addon == meta["addon"].lower():
                    flavor = f
                    break
            if not flavor:
                return []
            # The name carries no version, so the toc must. A made-up "0"
            # would compare equal to the next release and block it forever.
            ver = toc_version(root)
            if not ver:
                raise RuntimeError(
                    "cannot tell the version of %s: the file name has none "
                    "and the .toc has no ## Version" % os.path.basename(path))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if not force and not newer(ver, st["flavors"].get(flavor, {}).get("version")):
        log("%s %s already current (%s)" % (flavor, ver,
                                            st["flavors"][flavor]["version"]))
        return []

    tmp = tempfile.mkdtemp(prefix="addon-sync-%s-" % flavor, dir=STAGE)
    try:
        extract_archive(path, tmp)
        expected = FLAVORS[flavor]["addon"]
        root = find_addon_root(tmp, expected)
        if not root:
            raise RuntimeError("no .toc found in %s" % path)
        entry = publish(flavor, root, ver, path, "archive", cfg,
                        addon_name=addon_name_for(root, tmp, expected))
        st["flavors"][flavor] = entry
        save_state(st)
        log("published %s %s (%s)" % (flavor, ver, human(entry["size"])))
        return [entry]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _record_import_error(st, what, exc, verb="import"):
    log("%s %s failed: %s" % (verb, what, exc))
    msg = "%s %s: %s" % (verb, what, exc)
    st["last_error"] = ("%s; %s" % (st["last_error"], msg)
                        if st.get("last_error") else msg)


def import_archives(paths, cfg, st, force=False):
    """Import what is new. Failures land in st["last_error"] (joined)."""
    done = []
    by_flavor = {}
    for p in paths:
        flavor, ver = parse_release(os.path.basename(p))
        if flavor:
            if flavor not in by_flavor or newer(ver, by_flavor[flavor][0]):
                by_flavor[flavor] = (ver, p)
    for flavor in ORDER:
        if flavor in by_flavor:
            try:
                done += import_archive(by_flavor[flavor][1], cfg, st, force)
            except Exception as exc:
                _record_import_error(st, flavor, exc)
    # archives whose name did not identify a flavor
    for p in paths:
        if parse_release(os.path.basename(p))[0]:
            continue
        if archive_matches(os.path.basename(p)):
            try:
                done += import_archive(p, cfg, st, force)
            except Exception as exc:
                _record_import_error(st, os.path.basename(p), exc)
    return done


def build_manifest(cfg, st):
    pub_root = publish_dir(cfg)
    flavors = {}
    for flavor in ORDER:
        entry = st["flavors"].get(flavor)
        if entry:
            flavors[flavor] = dict(entry)
    data = {"generated": int(time.time()), "flavors": flavors}
    write_json(os.path.join(pub_root, "manifest.json"), data)
    return data


# ---------------------------------------------------------------- run

def _try_lock():
    """Take the cross-process check lock. Returns (locked, fh).

    (False, fh) when another check or import holds it. Where fcntl is
    missing (Windows) there is no lock to take; the caller proceeds, and
    the README says so. Callers close fh exactly once.
    """
    fh = open(LOCK, "w")
    if fcntl is None:
        return False, fh
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False, fh
    return True, fh


def run_check(force=False, triggered=False):
    cfg = config()
    incoming = incoming_dir(cfg)
    for d in (STATE_DIR, STAGE, incoming, publish_dir(cfg)):
        os.makedirs(d, exist_ok=True)
    locked, lock_fh = _try_lock()
    if fcntl is not None and not locked:
        log("another check is running; skipping")
        lock_fh.close()
        return {"ok": False, "error": "busy"}

    st = state()
    now = int(time.time())
    due = force or triggered or not st["last_check"] or (
        now - int(st["last_check"]) >= int(cfg["check_hours"]) * 3600 - 120)
    if not due:
        return {"ok": True, "skipped": "not due", "state": st}

    st["last_check"] = now
    save_state(st)
    log("check start (reader=browser)")
    try:
        feed = fetch_feed(cfg)
        # A thread page with release links but zero per-flavor items and no
        # attachment links means the browser is logged out (guests see
        # magnets only).
        st["needs_login"] = (feed["source"] == "browser"
                             and not feed["items"]
                             and not feed.get("bundles")
                             and not feed.get("attachments"))
        st["last_feed"] = {"source": feed["source"],
                           "fingerprint": feed["fingerprint"],
                           "items": len(feed["items"]),
                           "bundles": len(feed["bundles"])}
        if feed.get("browser_error"):
            st["last_feed"]["browser_error"] = feed["browser_error"]
            log("browser failed, manifest fallback: %s"
                % feed["browser_error"])
        save_state(st)

        archives = []
        # explicit per-flavor entries
        for item in sorted(feed["items"], key=lambda i: FLAVORS[i["flavor"]]["rank"]):
            flavor, ver = item["flavor"], item["version"]
            current = st["flavors"].get(flavor, {}).get("version")
            if not force and ver and not newer(ver, current):
                continue
            try:
                if item["kind"] == "magnet":
                    archives += enqueue_magnet(item["url"], cfg)
                elif item["kind"] == "http":
                    archives.append(http_download(item["url"], cfg, incoming))
                elif item["kind"] == "missing":
                    log("no link for %s %s" % (flavor, ver))
            except Exception as exc:
                _record_import_error(st, "%s %s" % (flavor, ver), exc,
                                     verb="download")

        # bundle magnets: download only when the info hash is new
        for bundle in feed["bundles"]:
            h = info_hash(bundle["url"])
            if not force and h in st["seen_magnets"]:
                continue
            try:
                if bundle["kind"] == "magnet":
                    archives += enqueue_magnet(bundle["url"], cfg)
                else:
                    archives.append(http_download(bundle["url"], cfg, incoming))
            except Exception as exc:
                _record_import_error(st, bundle.get("name") or h, exc,
                                     verb="download")
                continue
            st["seen_magnets"] = (st["seen_magnets"] + [h])[-20:]
            save_state(st)

        # archives already in the incoming folder, in case a fetch returned
        # only a summary
        if not archives:
            archives = [os.path.join(incoming, n)
                        for n in os.listdir(incoming)
                        if archive_matches(n)]

        archives = [p for p in archives if os.path.exists(p)]
        fetch_errors = st.get("last_error") or ""
        st["last_error"] = ""
        done = import_archives(archives, cfg, st, force=force)
        if fetch_errors:
            st["last_error"] = (fetch_errors + "; " + st["last_error"]
                                if st["last_error"] else fetch_errors)
        build_manifest(cfg, st)
        failed = st["last_error"]
        if not failed:
            st["last_ok"] = int(time.time())
        save_state(st)
        if failed:
            # The previous publish of a failed flavor is untouched; say so
            # loudly instead of reporting a healthy check.
            log("check done with errors: %d updated; %s" % (len(done), failed))
            return {"ok": False, "updated": done, "error": failed,
                    "state": st}
        log("check done: %d updated" % len(done))
        return {"ok": True, "updated": done, "state": st}
    except Exception as exc:
        low = str(exc).lower()
        st["needs_login"] = ("logged out" in low or "login" in low
                             or "no release links" in low)
        st["last_error"] = str(exc)
        save_state(st)
        log("check failed: %s" % exc)
        return {"ok": False, "error": str(exc),
                "needs_login": st["needs_login"]}
    finally:
        if locked:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


def import_from_downloads(force=False):
    cfg = config()
    incoming = incoming_dir(cfg)
    for d in (STATE_DIR, STAGE, publish_dir(cfg)):
        os.makedirs(d, exist_ok=True)
    locked, lock_fh = _try_lock()
    if fcntl is not None and not locked:
        log("another check is running; skipping import")
        lock_fh.close()
        return {"ok": False, "error": "busy"}
    try:
        st = state()
        paths = []
        for name in os.listdir(incoming):
            if archive_matches(name):
                paths.append(os.path.join(incoming, name))
        st["last_error"] = ""
        done = import_archives(paths, cfg, st, force=force)
        build_manifest(cfg, st)
        if not st["last_error"]:
            st["last_ok"] = int(time.time())
        save_state(st)
        out = {"ok": not st["last_error"], "updated": done}
        if st["last_error"]:
            out["error"] = st["last_error"]
        return out
    finally:
        if locked:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


# ---------------------------------------------------------------- status

def status_data():
    """Everything `status` prints and any front end needs: JSON-safe dict."""
    cfg = config()
    st = state()
    manifest = read_json(os.path.join(publish_dir(cfg), "manifest.json"),
                         {"flavors": {}})
    return {
        "ok": True,
        "now": int(time.time()),
        "last_check": st.get("last_check", 0),
        "last_ok": st.get("last_ok", 0),
        "last_error": st.get("last_error", ""),
        "needs_login": bool(st.get("needs_login")),
        "check_hours": cfg.get("check_hours", 24),
        "reader": "browser",
        "preset": PRESET.get("name") or PRESET_NAME,
        "thread_url": cfg.get("thread_url", ""),
        "flavors": manifest.get("flavors", {}),
        "order": ORDER,
        "labels": {f: FLAVORS[f]["label"] for f in ORDER},
    }


def request_check():
    """Start a check now: via the service, else a detached worker process."""
    try:
        subprocess.run(["/usr/bin/systemctl", "start",
                        "addonsync-feed.service"],
                       timeout=15, capture_output=True)
        return True
    except Exception:
        pass
    try:
        subprocess.Popen(
            [sys.executable, os.path.join(BASE_DIR, "worker.py"), "check",
             "--triggered"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- CLI

def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "status").lower()
    if cmd == "check":
        force = "--force" in argv
        # --scheduled is the systemd timer: it IS the schedule, so it runs
        # unconditionally (the random daily time comes from the timer unit).
        triggered = "--triggered" in argv or "--scheduled" in argv
        out = run_check(force=force, triggered=triggered)
    elif cmd == "import":
        out = import_from_downloads(force="--force" in argv)
    elif cmd == "status":
        out = status_data()
    elif cmd == "trigger":
        out = {"ok": request_check()}
    else:
        print("usage: worker.py [check|import|status|trigger] [--force]")
        return 2
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
