"""임시 공개 주소(터널) 오프라인 테스트 — 실제 cloudflared 는 실행하지 않는다."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animalpick import tunnel  # noqa: E402

CF_OUTPUT = """2026-11-22T05:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...
2026-11-22T05:00:01Z INF +--------------------------------------------------------------------------------------------+
2026-11-22T05:00:01Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
2026-11-22T05:00:01Z INF |  https://brave-otter-sample-words.trycloudflare.com                                        |
2026-11-22T05:00:01Z INF +--------------------------------------------------------------------------------------------+
"""


class FakeProc:
    def __init__(self, output):
        self.stderr = io.StringIO(output)
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class TunnelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.files = []
        for i in (1, 2):
            f = d / f"slide_0{i}.jpg"
            f.write_bytes(b"\xff\xd8jpeg" + bytes([i]))
            self.files.append(f)
        (d / "secret.txt").write_text("do not serve")
        self.exe = d / "cloudflared.exe"
        self.exe.write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_url_ignores_api_host(self):
        self.assertIsNone(tunnel.find_url("failed to reach https://api.trycloudflare.com/tunnel"))
        self.assertEqual(tunnel.find_url(CF_OUTPUT.splitlines()[3]), "https://brave-otter-sample-words.trycloudflare.com")

    def test_watch_output_reads_url(self):
        self.assertEqual(tunnel.watch_output(io.StringIO(CF_OUTPUT), timeout=2),
                         "https://brave-otter-sample-words.trycloudflare.com")

    def test_watch_output_fails_with_tail(self):
        with self.assertRaises(tunnel.TunnelError) as ctx:
            tunnel.watch_output(io.StringIO("ERR failed to unmarshal quick Tunnel\n"), timeout=2)
        self.assertIn("failed", str(ctx.exception))

    def test_file_server_serves_only_listed_files(self):
        server, prefix = tunnel.make_file_server(self.files)
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with urlopen(f"{base}{prefix}/slide_01.jpg") as r:
                self.assertEqual(r.headers["Content-Type"], "image/jpeg")
                self.assertTrue(r.read().startswith(b"\xff\xd8"))
            for bad in (f"{prefix}/secret.txt", "/slide_01.jpg", f"{prefix}/../secret.txt", "/"):
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(f"{base}{bad}")
                self.assertEqual(ctx.exception.code, 404)
            self.assertGreaterEqual(len(prefix), 33)  # 무작위 경로 (추측 불가)
        finally:
            server.shutdown()
            server.server_close()

    def test_public_urls_lifecycle(self):
        procs, checked = [], []

        def popen(args, **kw):
            self.assertIn("--url", args)
            self.assertTrue(args[args.index("--url") + 1].startswith("http://127.0.0.1:"))
            p = FakeProc(CF_OUTPUT)
            procs.append(p)
            return p
        with tunnel.public_urls(self.files, exe=self.exe, popen=popen, reach=checked.append) as urls:
            self.assertEqual(len(urls), 2)
            self.assertTrue(all(u.startswith("https://brave-otter-sample-words.trycloudflare.com/") for u in urls))
            self.assertTrue(urls[0].endswith("/slide_01.jpg"))
            self.assertFalse(procs[0].terminated)
        self.assertTrue(procs[0].terminated)  # 블록이 끝나면 닫힘
        self.assertEqual(checked, [urls[0]])

    def test_closed_on_error(self):
        procs = []

        def popen(args, **kw):
            procs.append(FakeProc(CF_OUTPUT))
            return procs[-1]
        with self.assertRaises(RuntimeError):
            with tunnel.public_urls(self.files, exe=self.exe, popen=popen, reach=lambda u: None):
                raise RuntimeError("게시 실패")
        self.assertTrue(procs[0].terminated)

    def test_missing_program(self):
        with self.assertRaises(tunnel.TunnelError):
            with tunnel.public_urls(self.files, exe=Path(self.tmp.name) / "nope.exe"):
                pass

    def test_wait_reachable_gives_up(self):
        def opener(req, timeout):
            raise HTTPError(req.full_url, 530, "origin", {}, io.BytesIO(b""))
        with self.assertRaises(tunnel.TunnelError):
            tunnel.wait_reachable("https://x.trycloudflare.com/a.jpg", tries=3, opener=opener, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
