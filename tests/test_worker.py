import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import worker  # noqa: E402


def make_addon(base, name="DemoAddon", version="11.2.0"):
    addon = os.path.join(base, name)
    os.makedirs(addon, exist_ok=True)
    with open(os.path.join(addon, name + ".toc"), "w", encoding="utf-8") as fh:
        fh.write("## Interface: 110200\n## Version: %s\n" % version)
    with open(os.path.join(addon, "core.lua"), "w", encoding="utf-8") as fh:
        fh.write("-- addon\n")
    return addon


class VersionTests(unittest.TestCase):
    def test_tuple_ignores_layout(self):
        self.assertEqual(worker.version_tuple("3.4.12"), (3, 4, 12))
        self.assertEqual(worker.version_tuple("v3-4_12"), (3, 4, 12))
        self.assertEqual(worker.version_tuple(""), ())

    def test_newer_pads_short_versions(self):
        self.assertTrue(worker.newer("3.4.12", "3.4.9"))
        self.assertTrue(worker.newer("3.5", "3.4.99"))
        self.assertFalse(worker.newer("3.4", "3.4.0"))
        self.assertFalse(worker.newer("3.4.1", "3.4.1"))
        self.assertTrue(worker.newer("1.0", ""))


class ReleaseNameTests(unittest.TestCase):
    def test_flavor_before_version(self):
        self.assertEqual(worker.parse_release("Demo Release Retail 11.2.0.zip"),
                         ("retail", "11.2.0"))
        self.assertEqual(worker.parse_release("Demo_Release_Classic_1_15_5.rar"),
                         ("classic", "1.15.5"))
        self.assertEqual(worker.parse_release("Demo-Release-Era-1.15.4.7z"),
                         ("era", "1.15.4"))

    def test_version_before_flavor(self):
        self.assertEqual(worker.parse_release("Demo Release 3.4.12 Anniversary.zip"),
                         ("anniversary", "3.4.12"))

    def test_unknown_names(self):
        self.assertEqual(worker.parse_release("DemoAddon-9.0.zip"),
                         (None, None))
        self.assertEqual(worker.parse_release("holiday-photos.zip"), (None, None))

    def test_clean_name_decodes_and_strips_extension(self):
        self.assertEqual(worker.clean_name("a%20b%2Dc.rar"), "a b c")


class CookieTests(unittest.TestCase):
    def test_page_host_from_the_feed_url(self):
        cfg = {"thread_url": "https://www.forum.example.com/threads/1-demo.html"}
        self.assertEqual(worker.page_host(cfg), "www.forum.example.com")
        self.assertEqual(worker.page_host({"thread_url": ""}), "")
        self.assertEqual(worker.page_host({}), "")

    def test_cookie_domain_matches_the_page_host(self):
        host = "www.forum.example.com"
        self.assertTrue(worker.cookie_matches(".forum.example.com", host))
        self.assertTrue(worker.cookie_matches("www.forum.example.com", host))
        self.assertFalse(worker.cookie_matches("other.example.com", host))
        self.assertFalse(worker.cookie_matches("", host))
        self.assertFalse(worker.cookie_matches(".forum.example.com", ""))


class MagnetTests(unittest.TestCase):
    def test_info_hash_prefers_btih(self):
        url = ("magnet:?xt=urn:btih:ABCDEF0123456789"
               "&dn=Demo%20Release%20Retail%2011.2.0")
        self.assertEqual(worker.info_hash(url), "abcdef0123456789")

    def test_info_hash_falls_back_to_sha1(self):
        digest = worker.info_hash("magnet:?dn=nohash")
        self.assertEqual(len(digest), 40)
        self.assertEqual(digest, hashlib.sha1(b"magnet:?dn=nohash").hexdigest())

    def test_display_name(self):
        url = "magnet:?xt=urn:btih:1&dn=Demo%20Release%20Retail%2011.2.0"
        self.assertEqual(worker.parse_magnet_name(url),
                         "Demo Release Retail 11.2.0")
        self.assertEqual(worker.parse_magnet_name("magnet:?xt=urn:btih:1"), "")


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="addonsync-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _zip(self, addon_name):
        src = make_addon(os.path.join(self.tmp, "src"), addon_name)
        path = os.path.join(self.tmp, addon_name + ".zip")
        with zipfile.ZipFile(path, "w") as zf:
            for root, _dirs, files in os.walk(src):
                for f in files:
                    full = os.path.join(root, f)
                    zf.write(full, os.path.join(addon_name,
                                                os.path.relpath(full, src)))
        return path, src

    def test_extract_zip_then_find_root(self):
        path, _src = self._zip("DemoAddon")
        dest = os.path.join(self.tmp, "out")
        worker.extract_archive(path, dest)
        root = worker.find_addon_root(dest, "DemoAddon")
        self.assertIsNotNone(root)
        self.assertEqual(os.path.basename(root), "DemoAddon")
        self.assertTrue(os.path.isfile(
            os.path.join(root, "DemoAddon.toc")))

    def test_find_addon_root_falls_back_to_toc_owner(self):
        dest = os.path.join(self.tmp, "mixed")
        make_addon(os.path.join(dest, "a"), "SomeOtherAddon")
        root = worker.find_addon_root(dest, "Missing")
        self.assertEqual(os.path.basename(root), "SomeOtherAddon")

    def test_make_zip_keeps_the_addon_folder(self):
        src = make_addon(os.path.join(self.tmp, "src"))
        path = os.path.join(self.tmp, "out.zip")
        worker.make_zip(src, path)
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        self.assertIn("DemoAddon/DemoAddon.toc", names)

    def test_extract_rejects_unknown_compression(self):
        path = os.path.join(self.tmp, "file.tar")
        with open(path, "wb") as fh:
            fh.write(b"not an archive")
        with mock.patch.object(worker.shutil, "which", return_value=None):
            with self.assertRaises(RuntimeError):
                worker.extract_archive(path, os.path.join(self.tmp, "x"))


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="addonsync-pub-")
        self.publish = os.path.join(self.tmp, "publish")
        self.cfg = {"publish_dir": self.publish, "keep_zips": 2}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_publish_writes_folder_zip_hash_and_marker(self):
        addon = make_addon(os.path.join(self.tmp, "src"))
        entry = worker.publish("retail", addon, "11.2.0", None, "archive",
                               self.cfg)

        folder = os.path.join(self.publish, "retail", "DemoAddon")
        self.assertTrue(os.path.isdir(folder))
        with open(os.path.join(folder, ".synced-version"),
                  encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "11.2.0")

        zip_path = os.path.join(self.publish, "retail", entry["zip"])
        self.assertTrue(os.path.isfile(zip_path))
        self.assertEqual(entry["sha256"], worker.sha256_file(zip_path))
        self.assertEqual(entry["size"], os.path.getsize(zip_path))
        self.assertEqual(entry["addon"], "DemoAddon")
        self.assertEqual(entry["flavor"], "retail")

    def test_manifest_is_the_contract_the_client_reads(self):
        addon = make_addon(os.path.join(self.tmp, "src"))
        entry = worker.publish("retail", addon, "11.2.0", None, "archive",
                               self.cfg)
        data = worker.build_manifest(self.cfg, {"flavors": {"retail": entry}})
        with open(os.path.join(self.publish, "manifest.json"),
                  encoding="utf-8") as fh:
            on_disk = json.load(fh)

        self.assertEqual(data, on_disk)
        got = on_disk["flavors"]["retail"]
        for key in ("addon", "version", "zip", "sha256", "size", "source"):
            self.assertIn(key, got)
        self.assertEqual(got["zip"], entry["zip"])

    def test_prune_keeps_the_newest(self):
        folder = os.path.join(self.tmp, "zips")
        os.makedirs(folder)
        start = time.time() - 400
        for i in range(4):
            path = os.path.join(folder, "a-%d.zip" % i)
            with open(path, "wb") as fh:
                fh.write(b"x")
            os.utime(path, (start + i, start + i))
        worker.prune(folder, r"^a-\d.*\.zip$", 2)
        self.assertEqual(sorted(os.listdir(folder)), ["a-2.zip", "a-3.zip"])


if __name__ == "__main__":
    unittest.main()
