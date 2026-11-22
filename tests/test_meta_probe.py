import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "meta_probe.py"
spec = importlib.util.spec_from_file_location("meta_probe", SCRIPT)
meta_probe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(meta_probe)
from animalpick import db  # noqa: E402

FAKE_TOKEN = "EAAfaketoken123456"

FAKE_DISCOVERY = {
    "business_discovery": {
        "username": "heodukgoo",
        "name": "허덕구",
        "followers_count": 1000,
        "media_count": 2,
        "id": "1789",
        "media": {"data": [
            {"id": "m1", "caption": "첫 글", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED",
             "timestamp": "2026-11-20T01:00:00+0000", "permalink": "https://www.instagram.com/p/a/",
             "like_count": 120, "comments_count": 8, "view_count": 3000},
            {"id": "m2", "caption": "둘째 글", "media_type": "IMAGE",
             "timestamp": "2026-11-19T01:00:00+0000", "permalink": "https://www.instagram.com/p/b/",
             "comments_count": 2},  # like_count 숨김, view_count 없음
        ]},
    },
    "id": "999",
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def ok_opener(payload):
    calls = []

    def opener(req, timeout):
        calls.append(req)
        return FakeResponse(json.dumps(payload).encode())
    opener.calls = calls
    return opener


def error_opener(code, message, status=400):
    def opener(req, timeout):
        body = json.dumps({"error": {"code": code, "message": message}}).encode()
        raise HTTPError(req.full_url, status, "err", {}, io.BytesIO(body))
    return opener


class FieldTests(unittest.TestCase):
    def test_page_fields(self):
        self.assertIn("instagram_business_account", meta_probe.page_fields())

    def test_business_discovery_fields(self):
        result = meta_probe.discovery_fields("heodukgoo", 10)
        self.assertIn("business_discovery.username(heodukgoo)", result)
        self.assertIn("media.limit(10)", result)
        for f in ("comments_count", "like_count", "view_count", "followers_count"):
            self.assertIn(f, result)
        self.assertEqual(result.count("{"), result.count("}"))

    def test_disallow_weird_handles(self):
        with self.assertRaises(ValueError):
            meta_probe.discovery_fields("heodukgoo)&access_token=abc", 10)

    def test_limit_validation(self):
        with self.assertRaises(ValueError):
            meta_probe.discovery_fields("heodukgoo", 0)

    def test_default_version_is_v27(self):
        with patch.dict("os.environ", {"META_GRAPH_API_VERSION": ""}):
            self.assertEqual(meta_probe.api_version(), "v27.0")


class GraphGetTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"META_ACCESS_TOKEN": FAKE_TOKEN, "META_GRAPH_API_VERSION": "v27.0"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_missing_token_never_calls_network(self):
        opener = ok_opener({})
        with patch.dict("os.environ", {"META_ACCESS_TOKEN": ""}):
            with self.assertRaises(RuntimeError):
                meta_probe.graph_get("me/accounts", meta_probe.page_fields(), opener=opener)
        self.assertEqual(opener.calls, [])

    def test_token_sent_in_header_not_url(self):
        opener = ok_opener({"id": "1"})
        meta_probe.graph_get("me", "id,name", opener=opener)
        req = opener.calls[0]
        self.assertNotIn(FAKE_TOKEN, req.full_url)
        self.assertEqual(req.get_header("Authorization"), f"Bearer {FAKE_TOKEN}")
        self.assertTrue(req.full_url.startswith("https://graph.facebook.com/v27.0/me?"))

    def test_token_redacted_from_response(self):
        payload = {"paging": {"next": f"https://graph.facebook.com/x?access_token={FAKE_TOKEN}&after=1"}}
        result = meta_probe.graph_get("me", "id", opener=ok_opener(payload))
        self.assertNotIn(FAKE_TOKEN, json.dumps(result))

    def test_token_redacted_from_error(self):
        with self.assertRaises(meta_probe.GraphError) as ctx:
            meta_probe.graph_get("me", "id", opener=error_opener(190, f"bad token {FAKE_TOKEN}"))
        self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
        self.assertEqual(ctx.exception.code, 190)
        self.assertIn("만료", str(ctx.exception))

    def test_permission_error_hint(self):
        with self.assertRaises(meta_probe.GraphError) as ctx:
            meta_probe.graph_get("me/accounts", "id", opener=error_opener(200, "perm", 403))
        self.assertIn("권한", str(ctx.exception))


class SummaryTests(unittest.TestCase):
    def test_pages_none(self):
        lines, ig = meta_probe.summarize_pages({"data": []})
        self.assertIsNone(ig)
        self.assertIn("0개", lines[0])

    def test_pages_unlinked(self):
        lines, ig = meta_probe.summarize_pages({"data": [{"id": "1", "name": "애니멀픽"}]})
        self.assertIsNone(ig)
        self.assertTrue(any("미연결" in l for l in lines))

    def test_pages_linked(self):
        payload = {"data": [{"id": "1", "name": "애니멀픽",
                             "instagram_business_account": {"id": "1784", "username": "animal..pickk"}}]}
        lines, ig = meta_probe.summarize_pages(payload)
        self.assertEqual(ig, "1784")
        self.assertTrue(any("META_IG_USER_ID=1784" in l for l in lines))

    def test_permissions(self):
        payload = {"data": [{"permission": "instagram_basic", "status": "granted"},
                            {"permission": "pages_show_list", "status": "declined"}]}
        text = "\n".join(meta_probe.summarize_permissions(payload))
        self.assertIn("[O] instagram_basic", text)
        self.assertIn("[X] pages_show_list", text)
        self.assertIn("빠진 권한", text)

    def test_probe_fields_matrix(self):
        def fake_get(path, fields):
            if "children" in fields:
                raise meta_probe.GraphError(400, 100, "-", "unsupported")
            return FAKE_DISCOVERY
        rows = meta_probe.probe_fields("heodukgoo", get=fake_get, ig_id="999")
        text = "\n".join(rows)
        self.assertIn("like_count=1/2", text)
        self.assertIn("view_count=1/2", text)
        self.assertTrue(any("children" in r and "실패 code=100" in r for r in rows))


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_save_and_upsert(self):
        r1 = db.save_discovery(self.conn, FAKE_DISCOVERY, api_version="v27.0", collected_at="2026-11-21T00:00:00+00:00")
        self.assertEqual((r1["posts_new"], r1["posts_updated"]), (2, 0))
        updated = json.loads(json.dumps(FAKE_DISCOVERY))
        updated["business_discovery"]["media"]["data"][0]["like_count"] = 200
        r2 = db.save_discovery(self.conn, updated, api_version="v27.0", collected_at="2026-11-22T00:00:00+00:00")
        self.assertEqual((r2["posts_new"], r2["posts_updated"]), (0, 2))

        rows = self.conn.execute("SELECT post_id, like_count, view_count, first_collected_at FROM reference_posts ORDER BY post_id").fetchall()
        self.assertEqual(rows[0], ("m1", 200, 3000, "2026-11-21T00:00:00+00:00"))
        self.assertEqual(rows[1][1:3], (None, None))  # 없는 값은 NULL, 추정 금지
        snaps = self.conn.execute("SELECT COUNT(*) FROM post_metric_snapshots").fetchone()[0]
        self.assertEqual(snaps, 4)
        acc = self.conn.execute("SELECT followers_count, access_status FROM reference_accounts").fetchone()
        self.assertEqual(acc, (1000, "ok"))

    def test_rejects_bad_payload(self):
        with self.assertRaises(ValueError):
            db.save_discovery(self.conn, {"id": "1"}, api_version="v27.0")


class CliTests(unittest.TestCase):
    def test_cli_without_token_fails_cleanly(self):
        with patch.dict("os.environ", {"META_ACCESS_TOKEN": "", "META_GRAPH_API_VERSION": ""}), \
             patch.object(meta_probe, "load_local_env", lambda: None), \
             patch("sys.stderr", new_callable=io.StringIO) as err:
            code = meta_probe.main(["pages"])
        self.assertEqual(code, 1)
        self.assertIn("META_ACCESS_TOKEN", err.getvalue())


if __name__ == "__main__":
    unittest.main()
