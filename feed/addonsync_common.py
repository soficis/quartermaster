"""Paths, JSON, preset lookup and logging shared by worker.py and reader.py.

Stdlib only, like the rest of feed/. Deploy it next to the two scripts.
"""

import json
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# All local state lives under one directory. Override it and the config path
# with the environment when the files live elsewhere (a service install
# points them at /var/lib/addon-sync and /etc/addon-sync.json). Defaults stay
# next to the scripts so a plain checkout works with zero setup.
STATE_DIR = (os.environ.get("ADDONSYNC_STATE_DIR") or
             os.path.join(BASE_DIR, "state"))
CONF = (os.environ.get("ADDONSYNC_CONFIG") or
        os.path.join(BASE_DIR, "config.json"))

# What the feed *is* -- which page, which addon folders, how release files are
# named -- lives in feeds/<name>.json. config.json picks one with "preset";
# ADDONSYNC_PRESET overrides it. A service install copies feeds/ next to the
# scripts; a checkout keeps it one level up.
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


def read_config(path):
    """config.json as a dict; missing, unreadable or non-object is {}."""
    data = read_json(path, {})
    return data if isinstance(data, dict) else {}


def is_unset(value):
    """"", [] and null mean "not set" in config.json and presets."""
    return value is None or value == "" or value == []


def preset_name(conf):
    return os.environ.get("ADDONSYNC_PRESET") or conf.get("preset") or DEF_PRESET


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


def append_log(path, msg):
    """Timestamp msg, append it to path (best effort) and print it."""
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)
