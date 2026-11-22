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
        self.assertEqual(worker.version_tuple("3.4.12"), ((3, ""), (4, ""), (12, "")))
        self.assertEqual(worker.version_tuple("v3-4_12"), worker.version_tuple("3.4.12"))
        self.assertEqual(worker.version_tuple(""), ())

    def test_newer_pads_short_versions(self):
        self.assertTrue(worker.newer("3.4.12", "3.4.9"))
        self.assertTrue(worker.newer("3.5", "3.4.99"))
        self.assertFalse(worker.newer("3.4", "3.4.0"))
        self.assertFalse(worker.newer("3.4.1", "3.4.1"))
        self.assertTrue(worker.newer("1.0", ""))

    def test_letter_suffix_is_a_later_build(self):
        self.assertTrue(worker.newer("1.5.2a", "1.5.2"))
        self.assertTrue(worker.newer("1.5.2b", "1.5.2a"))
        self.assertFalse(worker.newer("1.5.2a", "1.5.3"))
        self.assertFalse(worker.newer("1.5.2A", "1.5.2a"))


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

    def _extract_cmd(self, path, found):
        with mock.patch.object(worker.shutil, "which",
                               side_effect=lambda name: found.get(name)):
            with mock.patch.object(worker.subprocess, "run") as run:
                worker.extract_archive(path, os.path.join(self.tmp, "x"))
        return run.call_args[0][0]

    def test_extract_7z_accepts_homebrew_7zz(self):
        cmd = self._extract_cmd(os.path.join(self.tmp, "a.7z"),
                                {"7zz": "/opt/homebrew/bin/7zz"})
        self.assertEqual(cmd[0], "/opt/homebrew/bin/7zz")
        self.assertIn("-o" + os.path.join(self.tmp, "x"), cmd)

    def test_extract_7z_falls_back_to_unar(self):
        cmd = self._extract_cmd(os.path.join(self.tmp, "a.7z"),
                                {"unar": "/usr/local/bin/unar"})
        self.assertEqual(cmd, ["/usr/local/bin/unar", "-f", "-o",
                               os.path.join(self.tmp, "x"),
                               os.path.join(self.tmp, "a.7z")])

    def test_extract_rar_keeps_unrar_argument_order(self):
        cmd = self._extract_cmd(os.path.join(self.tmp, "a.rar"),
                                {"unrar": "/usr/bin/unrar"})
        self.assertEqual(cmd, ["/usr/bin/unrar", "x", "-o+", "-y",
                               os.path.join(self.tmp, "a.rar"),
                               os.path.join(self.tmp, "x") + os.sep])


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

    def test_keep_zips_zero_still_keeps_the_new_zip(self):
        addon = make_addon(os.path.join(self.tmp, "src"))
        cfg = dict(self.cfg, keep_zips=0)
        entry = worker.publish("retail", addon, "11.2.0", None, "archive", cfg)
        self.assertTrue(os.path.isfile(
            os.path.join(self.publish, "retail", entry["zip"])))

    def test_publish_refuses_symlinks(self):
        addon = make_addon(os.path.join(self.tmp, "src"))
        secret = os.path.join(self.tmp, "secret.txt")
        with open(secret, "w") as fh:
            fh.write("cookie")
        try:
            os.symlink(secret, os.path.join(addon, "leak.lua"))
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlinks here")
        with self.assertRaises(RuntimeError):
            worker.publish("retail", addon, "11.2.0", None, "archive",
                           self.cfg)
        self.assertFalse(os.path.exists(
            os.path.join(self.publish, "retail", "DemoAddon-11.2.0.zip")))

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

    def test_manifest_hash_matches_the_zip_the_client_resolves(self):
        addon = make_addon(os.path.join(self.tmp, "src"))
        entry = worker.publish("retail", addon, "11.2.0", None, "archive",
                               self.cfg)
        got = worker.build_manifest(self.cfg, {"flavors": {"retail": entry}})
        got = got["flavors"]["retail"]
        # sync.ps1 fetches <publish>/<flavor>/<zip> and checks sha256 + toc
        zip_path = os.path.join(self.publish, "retail", got["zip"])
        self.assertEqual(got["sha256"], worker.sha256_file(zip_path))
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        self.assertIn("%s/%s.toc" % (got["addon"], got["addon"]), names)
        self.assertIn("%s/.synced-version" % got["addon"], names)

    def test_publish_sweeps_old_stage_debris_only(self):
        dest = os.path.join(self.publish, "retail")
        os.makedirs(os.path.join(dest, ".new-old1"))
        os.makedirs(os.path.join(dest, ".old-young"))
        past = time.time() - 7200
        os.utime(os.path.join(dest, ".new-old1"), (past, past))
        worker.publish("retail", make_addon(os.path.join(self.tmp, "src")),
                       "11.2.0", None, "archive", self.cfg)
        left = os.listdir(dest)
        self.assertNotIn(".new-old1", left)
        self.assertIn(".old-young", left)


def zip_tree(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return path


class ConfigMergeTests(unittest.TestCase):
    def test_empty_config_values_keep_the_preset(self):
        base = {"thread_url": "https://forum.example.com/t/1",
                "downloader": {"method": "direct", "timeout_min": 5,
                               "command": ["x", "{magnet}"]}}
        example = {"thread_url": "", "publish_dir": "",
                   "downloader": {"method": "", "watch_dir": "",
                                  "command": []}}
        cfg = worker.merge_settings(base, example)
        self.assertEqual(cfg["thread_url"], "https://forum.example.com/t/1")
        self.assertEqual(cfg["downloader"]["method"], "direct")
        self.assertEqual(cfg["downloader"]["command"], ["x", "{magnet}"])
        self.assertNotIn("publish_dir", cfg)

    def test_config_downloader_changes_only_its_keys(self):
        base = {"downloader": {"method": "direct", "timeout_min": 5}}
        cfg = worker.merge_settings(base, {"downloader": {"method": "watchdir"}})
        self.assertEqual(cfg["downloader"], {"method": "watchdir",
                                             "timeout_min": 5})

    def test_shipped_example_does_not_blank_the_preset_url(self):
        example = worker.read_json(os.path.join(
            os.path.dirname(worker.BASE_DIR), "feed", "config.example.json"), {})
        cfg = worker.merge_settings(worker.DEFAULTS, example)
        self.assertEqual(cfg["thread_url"], worker.PRESET["thread_url"])


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="addonsync-imp-")
        self.cfg = {"publish_dir": os.path.join(self.tmp, "publish"),
                    "keep_zips": 2}
        self.stage = os.path.join(self.tmp, "stage")
        os.makedirs(self.stage)
        patcher = mock.patch.object(worker, "STAGE", self.stage)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _st(self):
        return {"flavors": {}, "last_error": ""}

    def test_unversioned_name_takes_the_toc_version_and_updates(self):
        st = self._st()
        a = zip_tree(os.path.join(self.tmp, "DemoAddon-1.2.0.zip"),
                     {"DemoAddon/DemoAddon.toc": "## Version: 1.2.0\n"})
        self.assertEqual(worker.parse_release(os.path.basename(a)), (None, None))
        first = worker.import_archives([a], self.cfg, st)
        self.assertEqual([e["version"] for e in first], ["1.2.0"])
        b = zip_tree(os.path.join(self.tmp, "DemoAddon-1.3.0.zip"),
                     {"DemoAddon/DemoAddon.toc": "## Version: 1.3.0\n"})
        second = worker.import_archives([b], self.cfg, st)
        self.assertEqual([e["version"] for e in second], ["1.3.0"])
        self.assertEqual(st["last_error"], "")

    def test_unversioned_name_and_toc_is_an_error_not_version_zero(self):
        st = self._st()
        a = zip_tree(os.path.join(self.tmp, "DemoAddon.zip"),
                     {"DemoAddon/DemoAddon.toc": "## Title: Demo\n"})
        self.assertEqual(worker.import_archives([a], self.cfg, st), [])
        self.assertIn("no ## Version", st["last_error"])
        self.assertNotIn("retail", st["flavors"])

    def test_flat_archive_publishes_under_the_addon_name(self):
        st = self._st()
        a = zip_tree(os.path.join(self.tmp, "Demo Release Retail 11.2.0.zip"),
                     {"DemoAddon.toc": "## Version: 11.2.0\n",
                      "core.lua": "-- x\n"})
        entry = worker.import_archives([a], self.cfg, st)[0]
        self.assertEqual(entry["addon"], "DemoAddon")
        self.assertEqual(entry["zip"], "DemoAddon-11.2.0.zip")
        self.assertTrue(os.path.isfile(os.path.join(
            self.cfg["publish_dir"], "retail", "DemoAddon", "DemoAddon.toc")))

    def test_newest_archive_per_flavor_wins(self):
        st = self._st()
        paths = [zip_tree(os.path.join(self.tmp, "Demo Release Retail %s.zip" % v),
                          {"DemoAddon/DemoAddon.toc": "## Version: %s\n" % v})
                 for v in ("11.2.0", "11.10.0", "11.9.1")]
        done = worker.import_archives(paths, self.cfg, st)
        self.assertEqual([e["version"] for e in done], ["11.10.0"])


class RunCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="addonsync-run-")
        self.incoming = os.path.join(self.tmp, "incoming")
        os.makedirs(self.incoming)
        cfg = dict(worker.DEFAULTS, publish_dir=os.path.join(self.tmp, "pub"),
                   downloads_dir=self.incoming)
        for name, value in (("STATE_DIR", self.tmp),
                            ("STATE", os.path.join(self.tmp, "state.json")),
                            ("LOCK", os.path.join(self.tmp, "lock")),
                            ("STAGE", os.path.join(self.tmp, "stage")),
                            ("LOG", os.path.join(self.tmp, "worker.log"))):
            p = mock.patch.object(worker, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(worker, "config", return_value=cfg)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _feed(self, **extra):
        feed = {"source": "browser", "fingerprint": "f", "items": [],
                "bundles": [], "attachments": 0}
        feed.update(extra)
        return feed

    def test_a_failed_import_is_reported_not_erased(self):
        with open(os.path.join(self.incoming,
                               "Demo Release Retail 11.2.0.zip"), "wb") as fh:
            fh.write(b"not a zip")
        with mock.patch.object(worker, "fetch_feed",
                               return_value=self._feed(attachments=1)):
            out = worker.run_check(force=True)
        self.assertFalse(out["ok"])
        st = worker.state()
        self.assertIn("retail", st["last_error"])
        self.assertEqual(st["last_ok"], 0)

    def test_bundles_only_is_not_a_login_problem(self):
        bundle = {"kind": "http", "url": "https://forum.example.com/a.zip"}
        with mock.patch.object(worker, "fetch_feed",
                               return_value=self._feed(bundles=[bundle])), \
                mock.patch.object(worker, "http_download",
                                  return_value=os.path.join(self.tmp, "gone.zip")):
            out = worker.run_check(force=True)
        self.assertTrue(out["ok"], out)
        self.assertFalse(worker.state()["needs_login"])

    def test_one_bad_bundle_does_not_abort_the_check(self):
        bad = {"kind": "magnet", "url": "magnet:?xt=urn:btih:BAD",
               "name": "bad bundle"}
        good = {"kind": "http", "url": "https://forum.example.com/g.zip",
                "name": "good bundle"}
        gone = os.path.join(self.tmp, "g.txt")  # dropped: not an archive
        with mock.patch.object(worker, "fetch_feed",
                               return_value=self._feed(bundles=[bad, good])), \
                mock.patch.object(worker, "enqueue_magnet",
                                  side_effect=RuntimeError("boom")), \
                mock.patch.object(worker, "http_download",
                                  return_value=gone) as dl:
            out = worker.run_check(force=True)
        dl.assert_called_once_with(good["url"], mock.ANY, mock.ANY, mock.ANY)
        self.assertFalse(out["ok"])
        self.assertIn("bad bundle", worker.state()["last_error"])

    def test_one_bad_item_does_not_abort_the_check(self):
        bad = {"flavor": "retail", "version": "11.2.0",
               "url": "magnet:?xt=urn:btih:BAD", "kind": "magnet",
               "name": "bad"}
        good = {"flavor": "classic", "version": "1.15.5",
                "url": "https://forum.example.com/g.zip", "kind": "http",
                "name": "good"}
        gone = os.path.join(self.tmp, "g.txt")  # dropped: not an archive
        with mock.patch.object(worker, "fetch_feed",
                               return_value=self._feed(items=[bad, good])), \
                mock.patch.object(worker, "enqueue_magnet",
                                  side_effect=RuntimeError("boom")), \
                mock.patch.object(worker, "http_download",
                                  return_value=gone) as dl:
            out = worker.run_check(force=True)
        dl.assert_called_once_with(good["url"], mock.ANY, mock.ANY, mock.ANY)
        self.assertFalse(out["ok"])
        self.assertIn("retail 11.2.0", worker.state()["last_error"])

    def test_import_refuses_while_a_check_holds_the_lock(self):
        fh = worker._try_lock()
        self.assertIsNotNone(fh)
        try:
            out = worker.import_from_downloads()
        finally:
            worker._unlock(fh)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "busy")
        # released: the next run gets it
        fh = worker._try_lock()
        self.assertIsNotNone(fh)
        worker._unlock(fh)

    def test_a_fixed_failure_stops_being_reported(self):
        bad = os.path.join(self.incoming, "Demo Release Retail 11.2.0.zip")
        with open(bad, "wb") as fh:
            fh.write(b"not a zip")
        with mock.patch.object(worker, "fetch_feed",
                               return_value=self._feed(attachments=1)):
            self.assertFalse(worker.run_check(force=True)["ok"])
            os.remove(bad)
            out = worker.run_check(force=True)
        self.assertTrue(out["ok"], out)
        st = worker.state()
        self.assertEqual(st["last_error"], "")
        self.assertGreater(st["last_ok"], 0)

    def test_not_due_releases_the_lock(self):
        with mock.patch.object(worker, "fetch_feed",
                               return_value=self._feed(attachments=1)):
            worker.run_check(force=True)
            out = worker.run_check()
        self.assertEqual(out.get("skipped"), "not due")
        fh = worker._try_lock()
        self.assertIsNotNone(fh)
        worker._unlock(fh)

    def test_logged_out_browser_with_manifest_fallback_needs_login(self):
        feed = self._feed(source="manifest", browser_error=(
            "browser: no magnet or attachment links found (logged out?)"))
        with mock.patch.object(worker, "fetch_feed", return_value=feed):
            worker.run_check(force=True)
        self.assertTrue(worker.state()["needs_login"])

    def test_direct_skipped_magnet_is_not_marked_seen(self):
        bundle = {"kind": "magnet", "url": "magnet:?xt=urn:btih:ABC",
                  "name": "b"}
        with mock.patch.object(worker, "fetch_feed",
                               return_value=self._feed(bundles=[bundle])),                 mock.patch.object(worker, "enqueue_magnet", return_value=[]):
            worker.run_check(force=True)
        self.assertNotIn("abc", worker.state()["seen_magnets"])

    def test_import_creates_a_missing_incoming_folder(self):
        shutil.rmtree(self.incoming)
        out = worker.import_from_downloads()
        self.assertTrue(out["ok"], out)


class FetchBrowserTests(unittest.TestCase):
    def _fetch(self, raw):
        fake = mock.Mock()
        fake.read_feed.return_value = raw
        with mock.patch.dict(sys.modules, {"reader": fake}):
            return worker.fetch_browser({})

    def test_url_fallback_classifies_opaque_names(self):
        # The label carries no release but the URL does. (None, None) is
        # truthy, so `parse_release(name) or parse_release(url)` could never
        # fall through to the URL; the two-step form does.
        raw = {"magnets": [],
               "attachments": [{
                   "url": "https://forum.example.com/forum/attachments/"
                          "Demo.Release.Retail.11.2.0.zip?id=5",
                   "name": "download"}]}
        feed = self._fetch(raw)
        self.assertEqual([(i["flavor"], i["version"]) for i in feed["items"]],
                         [("retail", "11.2.0")])

    def test_unclassified_attachments_become_bundles(self):
        raw = {"magnets": [],
               "attachments": [{
                   "url": "https://forum.example.com/forum/attachments/9",
                   "name": "misc files"}]}
        feed = self._fetch(raw)
        self.assertEqual(feed["items"], [])
        self.assertEqual(len(feed["bundles"]), 1)
        self.assertEqual(feed["bundles"][0]["kind"], "http")


class PresetFlavorTests(unittest.TestCase):
    def test_missing_rank_and_label_get_defaults(self):
        flavors = worker._load_flavors(
            {"Retail": {"addon": "DemoAddon", "wow": "_retail_"}})
        self.assertEqual(flavors["retail"]["rank"], 99)
        self.assertEqual(flavors["retail"]["label"], "Retail")

    def test_missing_wow_or_a_path_is_refused(self):
        for meta in ({"addon": "DemoAddon"},
                     {"addon": "DemoAddon", "wow": "../_retail_"}):
            with self.assertRaises(SystemExit):
                worker._load_flavors({"retail": meta})


class HttpDownloadNamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="addonsync-dl-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _dl(self, url, headers, body=b"fake-archive-bytes", cookies=(),
            taken=None):
        from email.message import Message
        msg = Message()
        for key, value in headers.items():
            msg[key] = value

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        resp = Resp(body)
        resp.headers = msg
        self.sent = None

        def urlopen(req, timeout=None):
            self.sent = req
            return resp

        with mock.patch.object(worker, "browser_cookies",
                               return_value=list(cookies)), \
                mock.patch.object(worker.urllib.request, "urlopen",
                                  side_effect=urlopen):
            return worker.http_download(
                url, {"thread_url": "https://forum.example.com/t/1"}, self.tmp,
                taken)

    def test_same_name_twice_in_one_run_keeps_both(self):
        taken = set()
        hdr = {"Content-Disposition": 'attachment; filename="Demo.zip"'}
        first = self._dl("https://forum.example.com/attachments/1/", hdr,
                         body=b"PKone", taken=taken)
        second = self._dl("https://forum.example.com/attachments/2/", hdr,
                          body=b"PKtwo", taken=taken)
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith("Demo-2.zip"))
        with open(first, "rb") as fh:
            self.assertEqual(fh.read(), b"PKone")

    def test_content_disposition_names_opaque_urls(self):
        dest = self._dl("https://forum.example.com/forum/attachments/12345/",
                        {"Content-Disposition":
                         'attachment; filename="Demo Release Retail 11.2.0.zip"'})
        self.assertTrue(worker.archive_matches(os.path.basename(dest)))
        self.assertTrue(dest.endswith("Demo Release Retail 11.2.0.zip"))
        self.assertTrue(os.path.isfile(dest))

    def test_content_type_sniffs_extension_when_nameless(self):
        dest = self._dl("https://forum.example.com/forum/attachments/Demo12345",
                        {"Content-Type": "application/zip"})
        self.assertTrue(dest.endswith(".zip"))
        self.assertTrue(worker.archive_matches(os.path.basename(dest)))

    def test_plain_url_tail_is_kept(self):
        dest = self._dl("https://cdn.example.com/Demo.Release.Retail.11.2.0.zip",
                        {"Content-Type": "application/octet-stream"})
        self.assertTrue(dest.endswith("Demo.Release.Retail.11.2.0.zip"))

    def test_a_non_archive_attachment_is_discarded(self):
        dest = self._dl("https://forum.example.com/forum/attachments/shot-png.9/",
                        {"Content-Type": "image/png"}, body=b"\x89PNG....")
        self.assertIsNone(dest)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_an_unnamed_archive_is_recognised_by_its_bytes(self):
        dest = self._dl("https://forum.example.com/forum/attachments/77/",
                        {"Content-Type": "application/octet-stream"},
                        body=b"Rar!\x1a\x07\x00rest")
        self.assertTrue(dest.endswith(".rar"), dest)

    def test_session_cookies_stay_on_their_own_host(self):
        jar = [{"name": "xf_session", "value": "s",
                "domain": ".forum.example.com"}]
        self._dl("https://cdn.other.example/Demo.Release.Retail.11.2.0.zip",
                 {}, cookies=jar)
        self.assertIsNone(self.sent.get_header("Cookie"))
        self._dl("https://forum.example.com/attachments/1/",
                 {"Content-Type": "application/zip"}, cookies=jar)
        self.assertEqual(self.sent.get_header("Cookie"), "xf_session=s")


if __name__ == "__main__":
    unittest.main()
