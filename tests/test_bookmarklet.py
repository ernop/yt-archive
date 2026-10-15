from __future__ import annotations

import io
import re
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from yt_archive import db
from yt_archive.jobs import JobQueue
from yt_archive.paths import bookmarklet_key
from yt_archive.web import make_handler


class BookmarkletTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        # Use the real submission path without starting the media worker.
        queue = JobQueue.__new__(JobQueue)
        queue.data_dir = self.data
        queue._cv = threading.Condition()
        self.handler_class = make_handler(self.data, queue, Mock())
        self.key = bookmarklet_key(self.data)

    def get(self, path, host="127.0.0.1:8765"):
        handler = self.handler_class.__new__(self.handler_class)
        handler.path = path
        handler.headers = {"Host": host}
        handler.rfile = io.BytesIO()
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue().decode()

    def test_key_is_created_once_and_private(self):
        path = self.data / "bookmarklet.key"
        self.assertGreaterEqual(len(self.key), 32)
        self.assertEqual(bookmarklet_key(self.data), self.key)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.data.iterdir() if p.name.startswith(".")), [])

    def test_home_page_offers_the_bookmarklet_only_under_a_local_host(self):
        for host in ("127.0.0.1:8765", "localhost:8765", "ytarchive.localhost"):
            with self.subTest(host=host):
                code, html = self.get("/", host)
                self.assertEqual(code, 200)
                self.assertIn(f'id="bookmarklet" href="#" data-key="{self.key}"', html)
                self.assertIn("location.origin + '/add'", html)
        code, html = self.get("/", "rebound.evil.test:8765")
        self.assertEqual(code, 403)
        self.assertNotIn(self.key, html)
        self.assertNotIn('id="bookmarklet"', html)

    def test_matching_key_opens_a_page_that_queues_through_the_normal_endpoint(self):
        code, html = self.get(
            "/add?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dchoose00001%26t%3D5"
            "&title=%283%29+A+%3Cb%3Evideo%3C%2Fb%3E+-+YouTube&key=" + self.key
        )
        self.assertEqual(code, 200)
        self.assertIn('data-url="https://www.youtube.com/watch?v=choose00001&amp;t=5"', html)
        self.assertIn('<p class="add-title">A &lt;b&gt;video&lt;/b&gt;</p>', html)
        self.assertIn("fetch('/api/get'", html)
        self.assertIn("window.close()", html)
        self.assertEqual(db.list_jobs(self.data), [])

    def test_wrong_missing_or_rebound_key_queues_nothing(self):
        for path, host in [
            ("/add?url=choose00001&key=wrong", "127.0.0.1:8765"),
            ("/add?url=choose00001", "127.0.0.1:8765"),
            ("/add?url=choose00001&key=%E2%9C%93", "127.0.0.1:8765"),
            ("/add?url=choose00001&key=" + self.key, "rebound.evil.test:8765"),
        ]:
            with self.subTest(path=path, host=host):
                code, html = self.get(path, host)
                self.assertEqual(code, 403)
                if host.startswith("127."):
                    self.assertIn("key doesn’t match", html)
                self.assertNotIn("/api/get", html)
                self.assertIsNone(re.search(r"data-url=", html))
        self.assertEqual(db.list_jobs(self.data), [])


if __name__ == "__main__":
    unittest.main()
