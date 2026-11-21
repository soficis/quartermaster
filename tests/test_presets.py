"""Feed presets are data: every shipped one must be loadable and complete."""

import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import ROOT, worker  # noqa: E402

FEEDS = sorted(
    p for p in glob.glob(os.path.join(ROOT, "feeds", "*.json"))
    if not os.path.basename(p).startswith("local-")
)


def load(path):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


class PresetFileTests(unittest.TestCase):
    def test_at_least_the_two_examples_ship(self):
        names = [os.path.basename(p) for p in FEEDS]
        self.assertIn("five-flavors-example.json", names)
        self.assertIn("generic-example.json", names)

    def test_every_preset_is_complete(self):
        for path in FEEDS:
            data = load(path)
            self.assertIsInstance(data, dict, path)
            self.assertTrue(data.get("name"), path)
            flavors = data.get("flavors")
            self.assertIsInstance(flavors, dict, path)
            self.assertTrue(flavors, path)
            for name, meta in flavors.items():
                for key in ("label", "addon", "wow"):
                    self.assertTrue(meta.get(key), "%s: %s.%s" % (path, name, key))
                self.assertIsInstance(meta.get("rank", 0), int, path)

    def test_five_flavor_example_carries_five_flavors(self):
        data = load(os.path.join(ROOT, "feeds", "five-flavors-example.json"))
        self.assertEqual(sorted(data["flavors"]),
                         ["anniversary", "classic", "era", "forever", "retail"])
        self.assertTrue(data["release_prefix"])
        self.assertEqual(data["downloader"]["method"], "direct")

    def test_generic_example_needs_no_browser_or_torrent(self):
        data = load(os.path.join(ROOT, "feeds", "generic-example.json"))
        self.assertEqual(data["downloader"]["method"], "direct")
        self.assertEqual(len(data["flavors"]), 1)
        self.assertEqual(data["thread_url"], "")


class PresetLookupTests(unittest.TestCase):
    def test_by_name_and_by_file(self):
        for name in ("five-flavors-example", "five-flavors-example.json"):
            path = worker.preset_path(name)
            self.assertTrue(path and os.path.isfile(path), name)
            self.assertEqual(os.path.basename(path), "five-flavors-example.json")

    def test_absolute_path_wins(self):
        path = os.path.join(ROOT, "feeds", "generic-example.json")
        self.assertEqual(worker.preset_path(path), path)

    def test_missing_preset_is_none(self):
        self.assertIsNone(worker.preset_path("no-such-feed-anywhere"))

    def test_env_selects_the_preset(self):
        # support.py points ADDONSYNC_PRESET at tests/preset.json
        self.assertEqual(worker.PRESET.get("name"), "test example")
        self.assertEqual(sorted(worker.FLAVORS),
                         ["anniversary", "classic", "era", "retail"])


if __name__ == "__main__":
    unittest.main()
