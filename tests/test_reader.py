import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import reader  # noqa: E402

BASE = "https://forum.example.com/threads/1-demo.html"

HTML = """
<div class="post">
  <a href="/forum/attachments/123-zip.456/?token=abc&amp;x=1">
     <span>Demo Release Retail 11.2.0.zip</span></a>
  <a href="https://forum.example.com/forum/attachments/123-zip.456/?token=abc&x=1">
     duplicate</a>
  <a href='magnet:?xt=urn:btih:ABC&amp;dn=Demo%20Release%20Classic%201.15.5'>m</a>
  <a href='magnet:?xt=urn:btih:ABC&amp;dn=Demo%20Release%20Classic%201.15.5'>m</a>
  <a href="/forum/attachments/999-other.zip">Another</a>
</div>
"""


class ExtractLinksTests(unittest.TestCase):
    def setUp(self):
        self.magnets, self.attachments = reader.extract_links(HTML, BASE)

    def test_magnets_are_deduplicated_and_named(self):
        self.assertEqual(len(self.magnets), 1)
        self.assertTrue(self.magnets[0]["url"].startswith("magnet:?xt=urn:btih:"))
        self.assertEqual(self.magnets[0]["name"],
                         "Demo Release Classic 1.15.5")

    def test_attachments_are_absolute_and_deduplicated(self):
        urls = [a["url"] for a in self.attachments]
        self.assertEqual(len(urls), 2)
        self.assertTrue(urls[0].startswith(
            "https://forum.example.com/forum/attachments/"))
        self.assertIn("&x=1", urls[0])
        self.assertNotIn("&amp;", urls[0])

    def test_attachment_label_loses_tags_and_whitespace(self):
        self.assertEqual(self.attachments[0]["name"],
                         "Demo Release Retail 11.2.0.zip")

    def test_empty_page_is_empty_not_an_error(self):
        magnets, attachments = reader.extract_links("<html></html>", BASE)
        self.assertEqual((magnets, attachments), ([], []))

    def test_single_quoted_attachment_href_is_found(self):
        html = ("<a href='/forum/attachments/77-x.zip'>"
                "Demo Release Retail 11.2.0.zip</a>")
        magnets, attachments = reader.extract_links(html, BASE)
        self.assertEqual(len(attachments), 1)
        self.assertTrue(attachments[0]["url"].endswith(
            "/forum/attachments/77-x.zip"))
        self.assertEqual(attachments[0]["name"],
                         "Demo Release Retail 11.2.0.zip")


class DisplayNameTests(unittest.TestCase):
    def test_dn_is_decoded(self):
        self.assertEqual(
            reader._dn("magnet:?xt=urn:btih:1&dn=Demo%20Release%20Era%201.15"),
            "Demo Release Era 1.15")

    def test_missing_dn_is_empty(self):
        self.assertEqual(reader._dn("magnet:?xt=urn:btih:1"), "")


class FakeCdp:
    """Records Network.setCookie params; everything else succeeds."""

    def __init__(self, calls):
        self.calls = calls

    def call(self, method, params=None, timeout=None):
        self.calls.append((method, params or {}))
        if method == "Network.setCookie":
            return {"success": True}
        return {}

    def close(self):
        pass


class ImportCookiesExpiryTests(unittest.TestCase):
    def _run(self, cookies):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as fh:
            json.dump({"cookies": cookies}, fh)
            path = fh.name
        self.addCleanup(os.unlink, path)
        calls = []
        feed = {"attachments": [{"url": "x", "name": "y"}], "magnets": []}
        with mock.patch.object(reader, "ensure_browser", return_value=9333), \
                mock.patch.object(reader, "_cdp",
                                  return_value=FakeCdp(calls)), \
                mock.patch.object(reader, "read_feed", return_value=feed), \
                mock.patch("time.sleep", return_value=None):
            out = reader.import_cookies(path)
        sent = [p for m, p in calls if m == "Network.setCookie"]
        return out, sent

    def _cookie(self, **extra):
        base = {"name": "sid", "value": "abc",
                "domain": "forum.example.com", "path": "/"}
        base.update(extra)
        return base

    def test_session_cookie_sends_no_expiry(self):
        out, sent = self._run([self._cookie()])
        self.assertTrue(out["ok"])
        self.assertEqual(len(sent), 1)
        self.assertNotIn("expires", sent[0])
        self.assertNotIn("expirationDate", sent[0])

    def test_zero_expiry_is_a_session_cookie(self):
        out, sent = self._run([self._cookie(expires=0)])
        self.assertTrue(out["ok"])
        self.assertNotIn("expires", sent[0])
        self.assertNotIn("expirationDate", sent[0])

    def test_persistent_cookie_keeps_its_expiry(self):
        out, sent = self._run([self._cookie(expires=1893456000)])
        self.assertTrue(out["ok"])
        self.assertEqual(sent[0]["expires"], 1893456000.0)


if __name__ == "__main__":
    unittest.main()
