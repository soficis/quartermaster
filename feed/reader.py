#!/usr/bin/env python3
"""Chromium page reader for the addon feed.

Owns the only browser: Chromium with a persistent profile, DevTools on
127.0.0.1:9333. Reads the rendered thread DOM over the
DevTools protocol and returns the archive links. ``worker.py`` calls
:func:`read_feed`; nothing else needs the browser.

Cloudflare's interstitial and the forum session both live in the browser, so
no cookie files or HTTP impersonation are needed. Sign in once (``login``
below, or ``import-cookies`` with a session exported from your daily
browser) and the profile keeps the session for months.

On Linux an Xvfb display is used when available; otherwise Chromium runs
headless. On Windows/macOS it always runs headless.

Stdlib only: the DevTools WebSocket client is implemented with ``socket``.

CLI:
  reader.py read     ensure the browser, print the feed links as JSON
  reader.py browser  ensure the browser is running
  reader.py login    ensure running, start x11vnc, print sign-in instructions
  reader.py status   JSON: display/browser/page state
  reader.py stop     close Chromium (and x11vnc), keep Xvfb
"""

import base64
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = (os.environ.get("ADDONSYNC_STATE_DIR") or
             os.path.join(BASE_DIR, "state"))
CONF = (os.environ.get("ADDONSYNC_CONFIG") or
        os.path.join(BASE_DIR, "config.json"))
PROFILE = os.path.join(STATE_DIR, "browser-profile")
PIDFILE = os.path.join(STATE_DIR, "chromium.pid")
PORT = 9333
DISPLAY = ":99"
DOM_TIMEOUT = 60
LOG = os.path.join(STATE_DIR, "reader.log")

# Same feed definition worker.py reads; see feeds/*.json.
PRESET_DIRS = (os.path.join(BASE_DIR, "feeds"),
               os.path.join(os.path.dirname(BASE_DIR), "feeds"))
DEF_PRESET = "generic-example"


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def _config():
    try:
        with open(CONF, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cfg(key, default):
    """config.json, else the preset, else `default`, like worker.py's merge.

    Empty values ("", null, []) count as unset, so a verbatim copy of
    config.example.json ("browser_profile": "") keeps the default.
    """
    for source in (_config(), preset()):
        value = source.get(key)
        if value not in (None, "", []):
            return value
    return default


def preset_name():
    return (os.environ.get("ADDONSYNC_PRESET")
            or _config().get("preset") or DEF_PRESET)


def preset():
    """The feed definition: page URL, addon folders, release naming."""
    name = preset_name()
    paths = [name] if os.path.isfile(name) else [
        os.path.join(folder, cand)
        for folder in PRESET_DIRS
        for cand in (name, name + ".json")]
    for path in paths:
        try:
            with open(path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def thread_url():
    """The config wins; otherwise the preset supplies the page to read."""
    return cfg("thread_url", "")


def _host(url):
    try:
        return (urllib.parse.urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def page_host():
    """Host of the feed page (no port): matches tabs, cookies and status."""
    return _host(thread_url())


def on_page_host(url):
    """True when a tab URL is on the feed page's host, not merely mentions it."""
    host = page_host()
    return bool(host) and _host(url) == host


def cookie_domain():
    host = page_host()
    return "." + host if host else ""


def profile_dir():
    return cfg("browser_profile", PROFILE)


def debug_port():
    try:
        return int(cfg("browser_port", PORT))
    except (TypeError, ValueError):
        return PORT


def display():
    return cfg("browser_display", DISPLAY)


def dom_timeout():
    try:
        return int(cfg("browser_timeout", DOM_TIMEOUT))
    except (TypeError, ValueError):
        return DOM_TIMEOUT


# ---------------------------------------------------------------- processes

def pgrep(pattern):
    try:
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def ensure_display():
    """Start Xvfb when available. Returns True with a display, else False.

    Headless Chromium covers everything when there is no X server (Windows,
    macOS, minimal Linux). Cloudflare clears fine either way; VNC login
    needs the display path.
    """
    disp = display()
    if pgrep("Xvfb %s( |$)" % re.escape(disp)):
        return True
    if not shutil.which("Xvfb"):
        return False
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "xvfb.log"), "ab") as fh:
        subprocess.Popen(["Xvfb", disp, "-screen", "0", "1400x1000x24"],
                         stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)
    for _ in range(20):
        time.sleep(0.5)
        if pgrep("Xvfb %s( |$)" % re.escape(disp)):
            return True
    raise RuntimeError("Xvfb did not start on %s" % disp)


def chromium_exe():
    found = (shutil.which("chromium") or shutil.which("chromium-browser")
             or shutil.which("google-chrome") or shutil.which("chrome"))
    if found:
        return found
    cands = [
        "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/usr/local/bin/chromium", "/usr/local/bin/chrome",
        "/opt/google/chrome/chrome",
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for p in cands:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise RuntimeError("no Chromium/Chrome binary found; install Chromium "
                       "or Chrome first")


def debugger_targets(port):
    with urllib.request.urlopen("http://127.0.0.1:%d/json" % port,
                                timeout=5) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def debugger_url(port, want_thread=True):
    try:
        targets = debugger_targets(port)
    except (OSError, ValueError):
        return None
    pages = [t for t in targets
             if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if want_thread:
        for t in pages:
            if on_page_host(t.get("url")):
                return t["webSocketDebuggerUrl"]
        return None
    return pages[0]["webSocketDebuggerUrl"] if pages else None


def open_thread(port, url):
    qs = urllib.parse.urlencode({"url": url})
    req = urllib.request.Request("http://127.0.0.1:%d/json/new?%s" % (port, qs),
                                 data=b"", method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except OSError as exc:
        raise RuntimeError("could not open thread tab: %s" % exc)


def ensure_browser():
    """Chromium running with the thread open. Returns the debug port."""
    url = thread_url()
    if not url:
        raise RuntimeError("no thread_url: set it in config.json or the preset")
    has_display = ensure_display()
    port = debug_port()
    if debugger_url(port):
        return port
    prof = profile_dir()
    os.makedirs(prof, exist_ok=True)
    exe = chromium_exe()
    cmd = [exe, "--user-data-dir=" + prof,
           "--remote-debugging-port=%d" % port,
           "--no-first-run", "--no-default-browser-check",
           "--disable-dev-shm-usage", "--disable-gpu",
           "--password-store=basic", "--no-sandbox"]
    if has_display:
        env = dict(os.environ, DISPLAY=display())
        cmd += ["--window-size=1400,1000", url]
    else:
        env = dict(os.environ)
        cmd += ["--headless=new", url]
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "chromium.log"), "ab") as fh:
        proc = subprocess.Popen(cmd, env=env, stdout=fh, stderr=subprocess.STDOUT,
                                start_new_session=True)
    try:
        with open(PIDFILE, "w", encoding="ascii") as fh:
            fh.write(str(proc.pid))
    except OSError:
        pass
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(2)
        if debugger_url(port):
            return port
    # Browser is up but the tab is not on the thread (or not open at all).
    try:
        debugger_targets(port)
        open_thread(port, thread_url())
    except (OSError, ValueError, RuntimeError):
        pass
    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(2)
        if debugger_url(port):
            return port
    raise RuntimeError("chromium did not expose the thread page on :%d" % port)


def _listener_pid(port):
    """PID listening on port when the OS can tell us (netstat on Windows)."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    needle = ":%d" % port
    # No state check: the word is localized ("ABHÖREN", ...) on non-English
    # Windows, and a debug port never opens outbound twins to confuse it with.
    for line in out.splitlines():
        parts = line.split()
        if (len(parts) >= 5 and parts[0].upper() == "TCP"
                and parts[1].endswith(needle)):
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None


def _is_browser_pid(pid):
    """Never kill a stranger that holds the debug port or reused a stale pid."""
    if os.name != "nt":
        # Our browser is the one started with this debug port; ps works on
        # Linux and macOS alike.
        try:
            out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return "remote-debugging-port=%d" % debug_port() in out
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid,
                              "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    name = out.strip().strip('"').split('","')[0].lower()
    return any(word in name for word in
               ("chrome", "chromium", "msedge", "brave", "vivaldi"))


def _kill_pid(pid):
    """Terminate one process tree. Returns True when it is gone."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True)
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    for _ in range(10):
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def stop_browser():
    """Close the browser this tool started, plus x11vnc when it is running."""
    stopped = []
    try:
        with open(PIDFILE, encoding="ascii") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        pid = None
    if pid is None:
        pid = _listener_pid(debug_port())
    if pid and _is_browser_pid(pid):
        _kill_pid(pid)
        stopped.append(pid)
    # Older POSIX installs may predate the pid file; pkill is harmless there.
    if os.name != "nt" and shutil.which("pkill"):
        subprocess.run(["pkill", "-f",
                        "remote-debugging-port=%d" % debug_port()],
                       capture_output=True)
        if shutil.which("x11vnc"):
            subprocess.run(["pkill", "-f", "x11vnc .*%s" % display()],
                           capture_output=True)
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
    return {"ok": True, "stopped": stopped}


# ---------------------------------------------------------------- DevTools WS (stdlib)

def _ws_connect(host, port, path):
    sock = socket.create_connection((host, port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\n"
                  "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                  "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                  % (path, host, port, key)).encode())
    fh = sock.makefile("rb")
    status = fh.readline().decode("latin1")
    if "101" not in status:
        sock.close()
        raise RuntimeError("ws handshake: %s" % status.strip())
    while True:
        line = fh.readline().decode("latin1")
        if line in ("\r\n", "\n", ""):
            break
    return sock, fh


def _ws_send(sock, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    mask = os.urandom(4)
    n = len(data)
    if n < 126:
        head = bytes([0x81, 0x80 | n])
    elif n < 65536:
        head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
    else:
        head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
    sock.sendall(head + mask + bytes(b ^ mask[i % 4]
                                     for i, b in enumerate(data)))


def _ws_frame(fh):
    head = fh.read(2)
    if len(head) < 2:
        raise RuntimeError("browser closed the DevTools connection")
    op, b2 = head[0] & 0x0F, head[1]
    ln = b2 & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", fh.read(2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", fh.read(8))[0]
    key = fh.read(4) if b2 & 0x80 else None
    payload = b""
    while len(payload) < ln:
        chunk = fh.read(ln - len(payload))
        if not chunk:
            raise RuntimeError("browser closed the DevTools connection")
        payload += chunk
    if key:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return op, payload


class CDP:
    def __init__(self, ws_url):
        parts = urllib.parse.urlparse(ws_url)
        path = parts.path + ("?" + parts.query if parts.query else "")
        self.sock, self.fh = _ws_connect(parts.hostname, parts.port or 80,
                                          path or "/")
        self._id = 0

    def call(self, method, params=None, timeout=20):
        self._id += 1
        msg = {"id": self._id, "method": method}
        if params:
            msg["params"] = params
        _ws_send(self.sock, json.dumps(msg))
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RuntimeError("DevTools %s timed out" % method)
            self.sock.settimeout(remaining)
            try:
                op, payload = _ws_frame(self.fh)
            except socket.timeout:
                raise RuntimeError("DevTools %s timed out" % method)
            if op == 0x8:
                raise RuntimeError("browser closed the DevTools connection")
            if op == 0x9:  # ping -> pong
                mask = os.urandom(4)
                self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask +
                                   bytes(b ^ mask[i % 4]
                                         for i, b in enumerate(payload)))
                continue
            if op not in (0x1, 0x2):
                continue
            try:
                obj = json.loads(payload.decode("utf-8", "replace"))
            except ValueError:
                continue
            if obj.get("id") == self._id:
                if "error" in obj:
                    raise RuntimeError("DevTools %s: %s" % (method,
                                                            obj["error"]))
                return obj.get("result", {})
            # Event for another call: ignore.

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _cdp(ws_url):
    port = debug_port()
    if ws_url:
        return CDP(ws_url)
    url = debugger_url(port, want_thread=False)
    if not url:
        raise RuntimeError("no debuggable page on :%d" % port)
    return CDP(url)


def read_dom(ws_url=None, timeout=None):
    """Outer HTML of the thread page, waiting out the interstitial."""
    cdp = _cdp(ws_url)
    try:
        cdp.call("Runtime.enable", timeout=10)
        try:
            cdp.call("Network.enable", timeout=10)
        except RuntimeError:
            pass
        timeout = dom_timeout() if timeout is None else timeout
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            try:
                res = cdp.call("Runtime.evaluate",
                               {"expression":
                                "document.documentElement.outerHTML",
                                "returnByValue": True}, timeout=15)
                last = (res.get("result") or {}).get("value") or ""
            except RuntimeError:
                time.sleep(2)
                continue
            if len(last) > 20000 and "Just a moment" not in last:
                return last
            time.sleep(2)
        return last
    finally:
        cdp.close()


def import_cookies(path):
    """Set cookies (exported JSON) in the browser, then verify the thread.

    Returns {"ok", "set", "failed", "magnets", "attachments"}. Raises
    RuntimeError when nothing was set or the thread stays logged out.
    """
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("cookies", [])
    ensure_browser()
    cdp = _cdp(None)
    ok, failed = 0, []
    try:
        try:
            cdp.call("Network.enable", timeout=10)
        except RuntimeError:
            pass
        for c in data:
            if not c.get("name") or c.get("value") is None:
                continue
            params = {"name": c["name"], "value": str(c["value"]),
                      "domain": c.get("domain") or cookie_domain(),
                      "path": c.get("path") or "/"}
            if c.get("secure"):
                params["secure"] = True
            if c.get("httpOnly"):
                params["httpOnly"] = True
            # CDP takes `expires` (epoch seconds; omitted = session cookie),
            # not `expirationDate`. Sending expirationDate: 0 would mark a
            # session cookie as expired in 1970 and drop the login.
            try:
                exp = float(c.get("expires") or 0)
            except (TypeError, ValueError):
                exp = 0
            if exp > 0:
                params["expires"] = exp
            same = (c.get("sameSite") or "").strip()
            if same.lower() in ("strict", "lax", "none"):
                params["sameSite"] = same.capitalize()
            try:
                res = cdp.call("Network.setCookie", params, timeout=10)
                if res.get("success"):
                    ok += 1
                else:
                    failed.append(c["name"])
            except RuntimeError:
                failed.append(c["name"])
    finally:
        cdp.close()
    if not ok:
        raise RuntimeError("no cookies accepted (failed: %s)"
                           % ", ".join(failed[:5]))
    # The thread page rendered while logged out; reload so the new
    # session takes effect before reading.
    cdp2 = _cdp(None)
    try:
        try:
            cdp2.call("Page.enable", timeout=10)
        except RuntimeError:
            pass
        try:
            cdp2.call("Page.reload", {"ignoreCache": True}, timeout=15)
        except RuntimeError:
            pass
    finally:
        cdp2.close()
    time.sleep(6)
    feed = read_feed()
    if not feed["attachments"]:
        raise RuntimeError(
            "cookies set (%d) but the thread shows no attachments; "
            "the session may be rejected (sign in over VNC instead)" % ok)
    return {"ok": True, "set": ok, "failed": failed,
            "magnets": len(feed["magnets"]),
            "attachments": len(feed["attachments"])}


def browser_cookies(ws_url=None):
    """Cookies from the browser profile (for authenticated http fetches)."""
    cdp = _cdp(ws_url)
    try:
        try:
            cdp.call("Network.enable", timeout=10)
        except RuntimeError:
            pass
        res = cdp.call("Network.getAllCookies", timeout=15)
        out = []
        for c in res.get("cookies", []):
            if c.get("name") and c.get("value") is not None:
                out.append({"name": c["name"], "value": str(c["value"]),
                            "domain": c.get("domain") or cookie_domain(),
                            "path": c.get("path") or "/"})
        return out
    except RuntimeError:
        return []
    finally:
        cdp.close()


# ---------------------------------------------------------------- extraction

def _dn(url):
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        for key in ("dn", "name"):
            if qs.get(key):
                return qs[key][0]
    except ValueError:
        pass
    return ""


_ARCHIVE_HREF = re.compile(r"\.(?:zip|rar|7z)(?=$|[?#])", re.I)


def extract_links(html, base=None):
    """Raw links: magnets (+display names) and attachment hrefs."""
    base = base or thread_url()
    text = html.replace("&amp;", "&")
    magnets, seen = [], set()
    for m in re.findall(r"magnet:\?[^\s\"'<>\\]+", text):
        m = m.rstrip(").,;")
        if m in seen:
            continue
        seen.add(m)
        magnets.append({"url": m, "name": _dn(m)})
    attachments, seen_href = [], set()
    for href, href_sq, label in re.findall(
            r'''<a\b[^>]*?href=(?:"([^"]*)"|'([^']*)')[^>]*>(.*?)</a>''',
            text, re.I | re.S):
        href = href or href_sq
        # Forum attachments (opaque /attachments/ URLs) and plain links to
        # an archive file; anything else on the page is navigation.
        if "/attachments/" not in href and not _ARCHIVE_HREF.search(href):
            continue
        href = urllib.parse.urljoin(base, href.replace("&amp;", "&"))
        if href in seen_href:
            continue
        seen_href.add(href)
        label = re.sub(r"<[^>]+>", " ", label)
        label = re.sub(r"\s+", " ", label).strip()
        attachments.append({"url": href, "name": label})
    return magnets, attachments


def read_feed():
    """Ensure the browser, read the thread, return raw links.

    Returns {"source": "browser", "magnets": [...], "attachments": [...],
    "dom_len": N}. Raises RuntimeError when the page cannot be read.
    """
    port = ensure_browser()
    ws = debugger_url(port)
    if not ws:
        open_thread(port, thread_url())
        time.sleep(4)
        ws = debugger_url(port)
    if not ws:
        raise RuntimeError("thread page did not appear (logged out?)")
    dom = read_dom(ws)
    if not dom or len(dom) < 1000:
        raise RuntimeError("empty DOM from the browser")
    if "Just a moment" in dom:
        raise RuntimeError("Cloudflare challenge did not clear")
    magnets, attachments = extract_links(dom)
    log("dom %d bytes: %d magnets, %d attachments"
        % (len(dom), len(magnets), len(attachments)))
    if not magnets and not attachments:
        raise RuntimeError("no magnet or attachment links found (logged out?)")
    return {"source": "browser", "magnets": magnets,
            "attachments": attachments, "dom_len": len(dom)}


def status():
    port = debug_port()
    try:
        targets = debugger_targets(port)
        up = True
    except (OSError, ValueError):
        targets, up = [], False
    pages = [t.get("url") for t in targets if t.get("type") == "page"]
    return {"ok": True, "display": display(), "port": port,
            "browser": up, "profile": profile_dir(),
            "thread_open": any(on_page_host(u) for u in pages),
            "pages": pages}


# ---------------------------------------------------------------- CLI

def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "status").lower()
    if cmd == "read":
        print(json.dumps(read_feed(), indent=2, sort_keys=True))
        return 0
    if cmd == "browser":
        port = ensure_browser()
        print(json.dumps({"ok": True, "port": port}, indent=2))
        return 0
    if cmd == "login":
        ensure_browser()
        if not shutil.which("x11vnc"):
            print("x11vnc is not installed, so there is nothing to look at.\n"
                  "Sign in another way instead: on your daily PC run\n"
                  "  tools\\Export-Cookies.ps1\n"
                  "copy the JSON to this machine and run:\n"
                  "  python3 %s import-cookies <file>" % os.path.abspath(__file__))
            return 0
        subprocess.run(["pkill", "-f", "x11vnc .*%s" % display()],
                       capture_output=True)
        os.makedirs(STATE_DIR, exist_ok=True)
        subprocess.Popen(
            ["x11vnc", "-display", display(), "-localhost", "-rfbport", "5901",
             "-shared", "-forever", "-bg", "-o",
             os.path.join(STATE_DIR, "x11vnc.log")],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            start_new_session=True)
        time.sleep(1)
        print("VNC is on localhost:5901 (bound to loopback). From your PC:\n"
              "  ssh -L 5901:localhost:5901 <user>@<this-host>\n"
              "then point a VNC viewer at localhost:5901, sign in to\n"
              "the site in the open Chromium, and verify with:\n"
              "  python3 %s read" % os.path.abspath(__file__))
        return 0
    if cmd == "import-cookies":
        path = (argv[2] if len(argv) > 2 else
                os.path.join(STATE_DIR, "cookies.json"))
        try:
            print(json.dumps(import_cookies(path), indent=2, sort_keys=True))
            return 0
        except (OSError, RuntimeError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1
    if cmd == "status":
        print(json.dumps(status(), indent=2, sort_keys=True))
        return 0
    if cmd == "stop":
        print(json.dumps(stop_browser()))
        return 0
    print("usage: reader.py [read|browser|login|import-cookies [file]|status|stop]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
