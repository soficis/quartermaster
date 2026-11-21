"""Addon portal plugin: /addons page, /api/addons status, zip downloads.

Self-contained front end for one hosted setup: imports the feed worker
(worker.py, deployed next to portal.py) for state, config and publish paths,
and implements the page, status JSON, file serving and check-now trigger
here. Nothing in feed/ knows about this plugin.
"""

import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.parse

try:
    import worker as addons
except ImportError:  # worker not deployed; routes answer 503
    addons = None


def routes():
    return [("GET", "/addons"), ("GET", "/api/addons"),
            ("GET", "/addons/file/<flavor>/<name>"),
            ("POST", "/addons/check")]


def card():
    if not addons:
        return ""
    return ('<div class="card">\n  <h2>Addon addon sync</h2>\n'
            '  <p class="muted" style="margin-top:0">Watches the release feed, '
            'downloads new builds and publishes a clean zip for every flavor. '
            'Forever is handled first.</p>\n'
            '  <a class="btn primary" href="/addons">Open Addon sync</a>\n</div>\n')


def feed_status():
    cfg = addons.config()
    st = addons.state()
    manifest = addons.read_json(
        os.path.join(addons.publish_dir(cfg), "manifest.json"), {"flavors": {}})
    return {
        "ok": True,
        "now": int(time.time()),
        "last_check": st.get("last_check", 0),
        "last_ok": st.get("last_ok", 0),
        "last_error": st.get("last_error", ""),
        "needs_login": bool(st.get("needs_login")),
        "check_hours": cfg.get("check_hours", 24),
        "reader": "browser",
        "thread_url": cfg.get("thread_url", ""),
        "flavors": manifest.get("flavors", {}),
        "order": addons.ORDER,
        "labels": {f: addons.FLAVORS[f]["label"] for f in addons.ORDER},
    }


def resolve_file(path):
    """Map /addons/file/<flavor>/<name> to a real file, or None."""
    parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
    if len(parts) < 4 or parts[:2] != ["addons", "file"]:
        return None
    flavor, name = parts[2], parts[3]
    if flavor not in addons.FLAVORS:
        return None
    name = os.path.basename(name)
    if not name or name in (".", ".."):
        return None
    base = os.path.realpath(
        os.path.join(addons.publish_dir(addons.config()), flavor))
    full = os.path.realpath(os.path.join(base, name))
    if os.path.dirname(full) != base or not os.path.isfile(full):
        return None
    return full


def request_check():
    try:
        subprocess.run(["/usr/bin/systemctl", "start",
                        "cellproxy-addons.service"],
                       timeout=15, capture_output=True)
        return True
    except Exception:
        pass
    try:
        subprocess.Popen(
            [sys.executable, os.path.join(addons.BASE_DIR, "worker.py"),
             "check", "--triggered"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def page():
    cfg = addons.config()
    st = addons.state()
    data = feed_status()
    flavors = data["flavors"]
    now = int(time.time())
    last = st.get("last_check") or 0
    ok = st.get("last_ok") or 0
    nxt = last + int(cfg.get("check_hours", 24)) * 3600 if last else 0

    def when(ts):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "never"

    rows = []
    for flavor in addons.ORDER:
        meta = addons.FLAVORS[flavor]
        entry = flavors.get(flavor, {})
        if entry:
            dl = ('<a class="btn" href="/addons/file/%s/%s">Download zip</a>'
                  % (flavor, urllib.parse.quote(entry.get("zip", ""))))
            rows.append(
                "<tr><td><b>%s</b><br><span class='muted small'>%s</span></td>"
                "<td class='num'>%s</td><td class='num'>%s</td>"
                "<td class='muted small'>%s</td><td>%s</td></tr>"
                % (_esc(meta["label"]), _esc(entry.get("addon", "")),
                   _esc(entry.get("version", "")),
                   addons.human(entry.get("size")),
                   when(entry.get("published")), dl))
        else:
            rows.append("<tr><td><b>%s</b><br><span class='muted small'>%s</span></td>"
                        "<td class='num'>&mdash;</td><td class='num'>&mdash;</td>"
                        "<td class='muted small'>not published</td><td></td></tr>"
                        % (_esc(meta["label"]), _esc(meta["addon"])))
    banner = ""
    if st.get("needs_login"):
        banner = ('<div class="warn"><b>Login needed.</b> The feed browser is '
                  'not signed in to the feed page. Run '
                  '<code>reader.py login</code>, sign in over VNC, then press '
                  'Check now.</div>')
    elif st.get("last_error"):
        banner = ('<div class="warn"><b>Last check failed:</b> %s</div>'
                  % _esc(st["last_error"]))

    return """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Addon sync</title>
<style>
body{margin:0;background:#0f1216;color:#e8edf2;font:16px/1.5 system-ui,sans-serif}
main{max-width:880px;margin:0 auto;padding:28px 18px 60px}
a{color:#4da3ff}
.muted{color:#93a1af}
.small{font-size:.86rem}
.card{background:#171c22;border:1px solid #2a333d;border-radius:14px;padding:16px 18px;margin:14px 0}
table{width:100%%;border-collapse:collapse}
th,td{text-align:left;padding:10px 8px;border-bottom:1px solid #2a333d;vertical-align:top}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.btn{display:inline-block;background:#24303c;border:1px solid #2a333d;color:#e8edf2;padding:7px 12px;border-radius:9px;text-decoration:none;font-size:.88rem}
.btn.primary{background:#4da3ff;border-color:#4da3ff;color:#08121d;font-weight:600}
.warn{background:#3a2a12;border:1px solid #6b4a18;border-radius:12px;padding:12px 14px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.kv{background:#12171d;border:1px solid #2a333d;border-radius:10px;padding:10px 12px}
.kv b{display:block;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:#93a1af}
form{display:inline}
button{font:inherit;cursor:pointer}
code{background:#0b0f13;border:1px solid #2a333d;border-radius:6px;padding:1px 5px;font-size:.86em}
</style></head><body><main>
<h1>Addon sync</h1>
<p class="muted">The feed host reads the release thread with its own browser,
downloads new builds and publishes a clean zip for every flavor.</p>
%s
<div class="grid">
  <div class="kv"><b>Last check</b>%s</div>
  <div class="kv"><b>Last success</b>%s</div>
  <div class="kv"><b>Next due</b>%s</div>
  <div class="kv"><b>Every</b>%d h</div>
</div>
<div class="card">
  <h2>Flavors</h2>
  <table><thead><tr><th>Flavor</th><th>Version</th><th>Size</th><th>Published</th><th></th></tr></thead>
  <tbody>%s</tbody></table>
</div>
<div class="card">
  <h2>Actions</h2>
  <form method="post" action="/addons/check"><button class="btn primary" type="submit">Check now</button></form>
  <a class="btn" href="/api/addons">JSON status</a>
</div>
</main></body></html>""" % (
        banner, when(last), when(ok), when(nxt), int(cfg.get("check_hours", 24)),
        "".join(rows))


def handle_get(handler, path, query):
    if path == "/api/addons":
        if not addons:
            return handler._send(503, "{}", "application/json") or True
        return handler._send(200, json.dumps(feed_status()),
                             "application/json") or True
    if path == "/addons":
        if not addons:
            return handler._send(503, "addons module missing",
                                 "text/plain; charset=utf-8") or True
        return handler._send(200, page()) or True
    if path.startswith("/addons/file/"):
        if not addons:
            return handler._send(503, "addons module missing",
                                 "text/plain; charset=utf-8") or True
        fpath = resolve_file(path)
        if not fpath:
            return handler._send(404, "not found",
                                 "text/plain; charset=utf-8") or True
        try:
            size = os.path.getsize(fpath)
        except OSError:
            return handler._send(404, "not found",
                                 "text/plain; charset=utf-8") or True
        ctype = mimetypes.guess_type(fpath)[0] or "application/octet-stream"
        try:
            handler.send_response(200)
            handler.send_header("Content-Type", ctype)
            handler.send_header("Content-Length", str(size))
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            with open(fpath, "rb") as fh:
                while True:
                    chunk = fh.read(262144)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        return True
    return False


def handle_post(handler, path):
    if path == "/api/addons/manifest":
        # Retired with the old feed helper; the worker reads the thread
        # itself now. Gone, with a hint, so a stale client fails loudly.
        return handler._send(410, json.dumps(
            {"ok": False,
             "error": "feed push retired; the worker reads the thread itself"}),
            "application/json") or True
    if path == "/addons/check":
        if not addons:
            return handler._send(503, "{}", "application/json") or True
        request_check()
        handler.send_response(303)
        handler.send_header("Location", "/addons")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True
    return False
