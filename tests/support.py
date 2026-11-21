"""Shared setup for the test-suite.

The workers read their state directory at import time, so point it at a temp
folder before importing them. Nothing here starts a browser or a downloader.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(ROOT, "feed")
if FEED not in sys.path:
    sys.path.insert(0, FEED)

os.environ["ADDONSYNC_STATE_DIR"] = tempfile.mkdtemp(prefix="addonsync-ci-")
os.environ.pop("ADDONSYNC_CONFIG", None)
# A neutral feed, so the tests never depend on one real addon or forum.
os.environ["ADDONSYNC_PRESET"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "preset.json")

import reader  # noqa: E402  (path set above)
import worker  # noqa: E402
