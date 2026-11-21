"""Portal plugin discovery.

A plugin is any ``plugins/<name>.py`` module exposing some of:

  card()                        -> HTML string for the index page ("" for none)
  handle_get(handler, path, query)  -> True when it answered the GET request
  handle_post(handler, path)        -> True when it answered the POST request
  routes()                      -> [(method, path), ...] for status pages

Failing plugins never break the portal: import and per-request errors are
caught and ignored. Add a plugin by dropping in a file and restarting
cellproxy-web; no edit to portal.py needed.
"""

import os
import traceback


def _load():
    mods = []
    here = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(here)):
        if name.startswith("_") or not name.endswith(".py"):
            continue
        modname = "plugins." + name[:-3]
        try:
            __import__(modname)
            mods.append(__import__(modname, fromlist=["*"]))
        except Exception:
            traceback.print_exc()
    return mods


_PLUGINS = _load()


def _call(mod, func, *args):
    fn = getattr(mod, func, None)
    if not fn:
        return None
    try:
        return fn(*args)
    except Exception:
        traceback.print_exc()
        return None


def handle_get(handler, path, query):
    for mod in _PLUGINS:
        if _call(mod, "handle_get", handler, path, query) is True:
            return True
    return False


def handle_post(handler, path):
    for mod in _PLUGINS:
        if _call(mod, "handle_post", handler, path) is True:
            return True
    return False


def cards():
    out = []
    for mod in _PLUGINS:
        html = _call(mod, "card")
        if html:
            out.append(html)
    return "\n".join(out)


def routes():
    seen = []
    for mod in _PLUGINS:
        for route in _call(mod, "routes") or []:
            if route not in seen:
                seen.append(route)
    return seen
