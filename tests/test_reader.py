import os
import sys
import unittest

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


class DisplayNameTests(unittest.TestCase):
    def test_dn_is_decoded(self):
        self.assertEqual(
            reader._dn("magnet:?xt=urn:btih:1&dn=Demo%20Release%20Era%201.15"),
            "Demo Release Era 1.15")

    def test_missing_dn_is_empty(self):
        self.assertEqual(reader._dn("magnet:?xt=urn:btih:1"), "")


if __name__ == "__main__":
    unittest.main()
