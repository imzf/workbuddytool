"""小工具层：HTTP 探测、原子写、命令执行。"""

from __future__ import annotations

import http.server
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from wb_launcher import util


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b'{"ok":true}'

    def do_GET(self) -> None:  # noqa: N802 - http.server 要求的名字
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args) -> None:
        pass


class HttpProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_ok(self) -> None:
        _Handler.status, _Handler.body = 200, b'{"ok":true}'
        reachable, status, body = util.http_probe(f"http://127.0.0.1:{self.port}/healthz")
        self.assertTrue(reachable)
        self.assertEqual(status, 200)
        self.assertIn("ok", body)

    def test_service_unavailable_is_still_reachable(self) -> None:
        # 面板没有账号时 /healthz 返回 503，但进程是活的
        _Handler.status, _Handler.body = 503, b'{"healthy":0,"total":0}'
        reachable, status, body = util.http_probe(f"http://127.0.0.1:{self.port}/healthz")
        self.assertTrue(reachable)
        self.assertEqual(status, 503)
        self.assertIn("total", body)

    def test_connection_refused(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        reachable, status, _ = util.http_probe(f"http://127.0.0.1:{port}/healthz", timeout=1)
        self.assertFalse(reachable)
        self.assertEqual(status, 0)


class AtomicWriteTest(unittest.TestCase):
    def test_writes_and_sets_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sub" / "file.json"
            util.atomic_write_text(target, "hello", mode=0o600)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)

    def test_overwrites_without_leaving_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.txt"
            util.atomic_write_text(target, "a" * 100)
            util.atomic_write_text(target, "b")
            self.assertEqual(target.read_text(encoding="utf-8"), "b")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["file.txt"])


class RunTest(unittest.TestCase):
    def test_missing_command(self) -> None:
        proc = util.run(["definitely-not-a-command-xyz"])
        self.assertEqual(proc.returncode, 127)

    def test_capture_output(self) -> None:
        proc = util.run(["sh", "-c", "echo hi"])
        self.assertEqual(proc.stdout.strip(), "hi")

    def test_timeout(self) -> None:
        proc = util.run(["sleep", "5"], timeout=0.2)
        self.assertEqual(proc.returncode, 124)


class HumanSizeTest(unittest.TestCase):
    def test_units(self) -> None:
        self.assertEqual(util.human_size(512), "512 B")
        self.assertEqual(util.human_size(2048), "2.0 KB")
        self.assertEqual(util.human_size(5 * 1024 * 1024), "5.0 MB")


if __name__ == "__main__":
    unittest.main()
