#!/usr/bin/env python3
"""Migrate cellproxy portal.py from the inline Addon patch to plugins.

- Removes the old ``# >>> addons-sync ... # <<< addons-sync`` blocks.
- Adds a generic plugin loader (``plugins/*.py``) with hooks in do_GET,
  do_POST and the index page. Future plugins need no portal edits.

Run on the Pi:
    python3 /usr/local/libexec/cellproxy/patch_portal_plugins.py
Backs up portal.py first and refuses to write unless the result compiles.
Idempotent: running it twice changes nothing the second time.
"""

import os
import shutil
import sys
import time

TARGET = sys.argv[1] if len(sys.argv) > 1 else "/usr/local/libexec/cellproxy/portal.py"
BEGIN = "# >>> addons-sync"
END = "# <<< addons-sync"

LOADER = """try:
    import plugins as _cellplugins  # portal plugins (plugins/*.py)
except ImportError:  # portal works without plugins
    _cellplugins = None
"""

# Real hooks (kept as plain strings so anchors stay readable).
GET_HOOK_REAL = ("        if _cellplugins "
                 "and _cellplugins.handle_get(self, path, query):\n"
                 "            return\n")
POST_HOOK_REAL = ("        if _cellplugins "
                  "and _cellplugins.handle_post(self, path):\n"
                  "            return\n")
CARDS_LINE = ("    plugin_cards = "
              "_cellplugins.cards() if _cellplugins else \"\"\n")


def strip_blocks(text):
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        if line.strip() == BEGIN:
            inside = True
            continue
        if line.strip() == END:
            inside = False
            continue
        if not inside:
            out.append(line)
    return "".join(out)


def insert_after(text, anchor, block):
    idx = text.find(anchor)
    if idx < 0:
        raise SystemExit("anchor not found: %r" % anchor[:60])
    idx += len(anchor)
    return text[:idx] + "\n" + block + text[idx:]


def insert_before(text, anchor, block):
    idx = text.find(anchor)
    if idx < 0:
        raise SystemExit("anchor not found: %r" % anchor[:60])
    return text[:idx] + block + "\n" + text[idx:]


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit("expected one %r, found %d" % (old[:50],
                                                        text.count(old)))
    return text.replace(old, new, 1)


def main():
    with open(TARGET, encoding="utf-8") as fh:
        text = fh.read()
    original = text
    text = strip_blocks(text)

    if "_cellplugins" not in text:
        text = insert_after(text, "    uppages = None\n", LOADER)

    if "_cellplugins.handle_get" not in text:
        text = insert_before(
            text,
            '        return self._send(404, "not found", "text/plain; charset=utf-8")'
            "\n\n    def do_POST(self):",
            GET_HOOK_REAL)

    if "_cellplugins.handle_post" not in text:
        text = insert_before(
            text,
            '        if path != "/downloads" and path != "/uploads":',
            POST_HOOK_REAL)

    if "__PLUGIN_CARDS__" not in text:
        text = replace_once(text, "__ADDONS_CARD__\n", "__PLUGIN_CARDS__\n")

    if "plugin_cards = " not in text:
        text = insert_before(text, "    return (PAGE\n", CARDS_LINE)

    if '"__PLUGIN_CARDS__"' not in text:
        text = replace_once(
            text,
            '.replace("__ADDONS_CARD__", addons_card))',
            '.replace("__PLUGIN_CARDS__", plugin_cards))')

    if text == original:
        print("already migrated")
        return 0
    try:
        compile(text, TARGET, "exec")
    except SyntaxError as exc:
        print("migrated file does not compile: %s" % exc)
        return 1
    backup = "%s.bak-addons-plugins-%s" % (TARGET,
                                          time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(TARGET, backup)
    tmp = TARGET + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, TARGET)
    print("migrated %s (backup %s)" % (TARGET, backup))
    return 0


if __name__ == "__main__":
    sys.exit(main())
