"""주제 소싱 오프라인 테스트 — 네트워크·AI 호출 없음."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animalpick import db, sources  # noqa: E402

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>A fish as old as Montana lives over 100 years</title><link>https://ex.com/fish</link>
<pubDate>Fri, 21 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Climate talks stall again</title><link>https://ex.com/climate</link>
<pubDate>Fri, 21 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Old whale story</title><link>https://ex.com/old</link>
<pubDate>Mon, 01 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Penguins &amp; stones</title><link>https://ex.com/penguin</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Bizarre deep-sea fish with a see-through head</title><link href="https://ex.com/barreleye"/>
<updated>2026-11-20T08:00:00Z</updated></entry></feed>"""


class ParseTests(unittest.TestCase):
    def test_rss_and_atom(self):
        r = sources.parse_feed(RSS)
        self.assertEqual(len(r), 4)
        self.assertEqual(r[3]["title"], "Penguins & stones")
        self.assertIsNone(r[3]["published"])
        a = sources.parse_feed(ATOM)
        self.assertEqual(a[0]["link"], "https://ex.com/barreleye")
        self.assertEqual(a[0]["published"].year, 2026)

    def test_filters(self):
        self.assertTrue(sources.looks_animal("A fish as old as Montana"))
        self.assertFalse(sources.looks_animal("Climate talks stall again"))
        self.assertTrue(sources.looks_animal("돌고래 '안목이' 첫 위성신호"))
        self.assertFalse(sources.looks_animal("부산 자원봉사자 모집"))  # 봉사자 속 사자 오인 방지`n        self.assertTrue(sources.looks_animal("사자 무리가 사냥에 성공"))`n        self.assertFalse(sources.looks_animal("곰곰이 생각해 볼 정책"))
        self.assertTrue(sources.is_recent(None, NOW))
        self.assertFalse(sources.is_recent(NOW - timedelta(days=30), NOW))

    def test_title_key_strips_outlet(self):
        self.assertEqual(sources.title_key("멸종위기 물수리 포착 - KBS 뉴스"), sources.title_key("멸종위기 물수리 포착"))

    def test_collect_feeds_keeps_recent_animal_only(self):
        def fetch(url, headers=None):
            if "fail" in url:
                raise OSError("down")
            return RSS
        with patch.object(sources, "FEEDS", [("news_intl", "A", "https://a"), ("news_kr", "B", "https://fail")]):
            got = sources.collect_feeds(fetch=fetch, now=NOW, log=lambda m: None)
        self.assertEqual([g["link"] for g in got], ["https://ex.com/fish", "https://ex.com/penguin"])
        self.assertEqual(got[0]["kind"], "news_intl")


class NaverTests(unittest.TestCase):
    def test_skipped_without_key(self):
        called = []
        with patch.dict(os.environ, {"NAVER_CLIENT_ID": "", "NAVER_CLIENT_SECRET": ""}):
            self.assertEqual(sources.collect_naver(fetch=lambda *a: called.append(1), log=lambda m: None), [])
        self.assertEqual(called, [])

    def test_parses_and_sends_headers(self):
        seen = []
        body = {"items": [
            {"title": "<b>멸종위기</b> 저어새 다시 포착", "originallink": "https://news.kr/1", "link": "https://n.naver/1",
             "pubDate": "Sat, 22 Nov 2026 09:00:00 +0900"},
            {"title": "국회 예산안 처리 소식", "originallink": "https://news.kr/2", "pubDate": "Sat, 22 Nov 2026 09:00:00 +0900"},
        ]}

        def fetch(url, headers=None):
            seen.append(headers)
            return json.dumps(body).encode()
        with patch.dict(os.environ, {"NAVER_CLIENT_ID": "ID", "NAVER_CLIENT_SECRET": "SEC"}), \
             patch.object(sources, "NAVER_QUERIES", ["멸종위기 동물"]):
            got = sources.collect_naver(fetch=fetch, now=NOW, log=lambda m: None)
        self.assertEqual(seen[0], {"X-Naver-Client-Id": "ID", "X-Naver-Client-Secret": "SEC"})
        self.assertEqual(len(got), 1)  # 동물과 무관한 기사는 빠짐
        self.assertEqual(got[0]["title"], "멸종위기 저어새 다시 포착")
        self.assertEqual(got[0]["link"], "https://news.kr/1")
        self.assertEqual(got[0]["kind"], "news_kr")


class EvergreenTests(unittest.TestCase):
    def test_only_verified_wiki_and_unused(self):
        def asker(system, prompt):
            self.assertIn("Greenland shark", prompt)  # 이미 다룬 동물 목록 전달
            return json.dumps({"items": [
                {"subject": "Greenland shark", "wiki_title": "Greenland shark", "angle": "중복"},
                {"subject": "Axolotl", "wiki_title": "Axolotl", "angle": "재생"},
                {"subject": "Fakeasaurus", "wiki_title": "Fakeasaurus", "angle": "없는 문서"},
            ]})

        def fetch(url, headers=None):
            if "Fakeasaurus" in url:
                raise OSError("404")
            return json.dumps({"type": "standard", "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Axolotl"}}}).encode()
        got = sources.collect_evergreen(3, ["Greenland shark"], asker=asker, fetch=fetch, log=lambda m: None)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["link"], "https://en.wikipedia.org/wiki/Axolotl")
        self.assertEqual(got[0]["kind"], "evergreen")


def make_item(i, kind="news_intl", subject=None, title=None):
    t = title or f"Story {i} about a fish"
    return {"kind": kind, "source": "S", "title": t, "link": f"https://ex.com/{i}", "published_at": None,
            "id": sources.cand_id(f"https://ex.com/{i}"), "title_key": sources.title_key(t)}


class ScoreTests(unittest.TestCase):
    def test_score_exclude_and_event_photo(self):
        items = [make_item(0), make_item(1), make_item(2)]

        def asker(system, prompt):
            base = {"curiosity": 5, "emotion": 4, "share": 4, "korea": 3, "visual": 4, "factual": 5}
            return json.dumps({"items": [
                {"i": 0, **base, "subject": "Blue sucker", "photo_query": "blue sucker fish", "ko_title": "100살 물고기"},
                {"i": 1, **base, "exclude": True, "reason": "칼럼"},
                {"i": 2, **base, "needs_event_photo": True, "reason": "특정 개체"},
                {"i": 99, **base},  # 잘못된 번호는 무시
            ]})
        got = sources.score_items(items, asker=asker, log=lambda m: None)
        by = {g["link"]: g for g in got}
        self.assertEqual(by["https://ex.com/0"]["status"], "new")
        self.assertEqual(by["https://ex.com/0"]["score"], round(1.3 * 5 + 1.2 * 4 + 1.2 * 4 + 3 + 4 + 1.3 * 5, 1))
        self.assertEqual(by["https://ex.com/1"]["status"], "excluded")
        self.assertEqual(by["https://ex.com/2"]["status"], "excluded")
        self.assertIn("그 개체 사진", by["https://ex.com/2"]["reason"])

    def test_weighted_clamps_bad_values(self):
        self.assertEqual(sources.weighted({"curiosity": 99, "emotion": "x"}), 6.5)

    def test_check_photos(self):
        items = [
            {**make_item(0), "status": "new", "score": 30.0, "subject": "Owl", "photo_query": "owl", "reason": ""},
            {**make_item(1), "status": "new", "score": 29.0, "subject": "Rare frog", "photo_query": "rare frog", "reason": ""},
            {**make_item(2), "status": "new", "score": 28.0, "subject": "Bear", "photo_query": "bear", "reason": ""},
        ]

        def finder(q):
            if "owl" in q.lower():
                return [{"page_url": f"o{i}", "source": "wikimedia_commons", "title": "owl"} for i in range(8)]
            if "bear" in q.lower():
                return [{"page_url": "b1", "source": "pixabay", "title": "bear, animal"}]
            return []
        sources.check_photos(items, finder=finder, pause=lambda s: None, log=lambda m: None)
        self.assertEqual((items[0]["photos"], items[0]["score"], items[0]["status"]), (8, 30.0, "new"))
        self.assertEqual(items[1]["status"], "excluded")
        self.assertIn("사진 없음", items[1]["reason"])
        self.assertEqual((items[2]["photos"], items[2]["score"]), (1, 24.0))  # 사진 적으면 감점


class StoreAndPickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_dedupe_against_db_and_batch(self):
        a = {"kind": "news_intl", "source": "S", "title": "Owl story - Outlet", "link": "https://ex.com/a"}
        sources.save(self.conn, sources.dedupe([a], self.conn))
        again = sources.dedupe([
            a,                                                                         # 같은 링크
            {**a, "link": "https://other.com/a", "title": "Owl story"},               # 같은 제목(매체명만 다름)
            {**a, "link": "https://ex.com/b", "title": "New bear story"},
            {**a, "link": "https://ex.com/b2", "title": "New bear story"},           # 이번 수집 안 중복
        ], self.conn)
        self.assertEqual([x["link"] for x in again], ["https://ex.com/b"])

    def _cand(self, i, kind, score, subject, photos=5, status="new"):
        return {**make_item(i, kind), "score": score, "subject": subject, "photos": photos, "status": status,
                "ko_title": f"t{i}", "scores": {}, "reason": ""}

    def test_pick_daily_mix_fill_and_unique_subject(self):
        c = [self._cand(0, "news_intl", 30, "Owl"), self._cand(1, "news_intl", 29, "Owl"),  # 같은 동물 → 하나만
             self._cand(2, "news_intl", 28, "Bear"), self._cand(3, "news_kr", 20, "Otter"),
             self._cand(4, "evergreen", 25, "Axolotl"), self._cand(5, "news_intl", 27, "Fox"),
             self._cand(6, "news_intl", 26, "Frog", photos=0),                              # 사진 없음 → 제외
             self._cand(7, "news_intl", 24, "Crab"), self._cand(8, "news_intl", 23, "Seal"),
             self._cand(9, "news_intl", 22, "Eel")]
        picks = sources.pick_daily(c, {"news_intl": 2, "news_kr": 2, "evergreen": 1})
        subjects = [p["subject"] for p in picks]
        self.assertEqual(len(picks), 5)
        self.assertEqual(len(set(subjects)), 5)
        self.assertIn("Otter", subjects)
        self.assertIn("Axolotl", subjects)
        self.assertNotIn("Frog", subjects)
        # 국내가 1개뿐이라 남은 자리는 다른 종류 상위로 채움
        self.assertEqual(sum(1 for p in picks if p["kind"] == "news_intl"), 3)

    def test_pick_daily_same_animal_with_longer_name(self):
        c = [self._cand(0, "news_intl", 34, "Red crab"), self._cand(1, "news_intl", 31, "Christmas Island red crabs"),
             self._cand(2, "news_intl", 30, "Barreleye fish")]
        picks = sources.pick_daily(c, {"news_intl": 3})
        self.assertEqual([p["subject"] for p in picks], ["Red crab", "Barreleye fish"])

    def test_make_drafts_marks_status(self):
        c = self._cand(0, "news_intl", 30, "Owl")
        sources.save(self.conn, [c])
        calls = []

        def create(topic, link, text):
            calls.append((topic, link))
            return {"id": "20261122-000000-abcdef"}
        made = sources.make_drafts([c], conn=self.conn, create=create, log=lambda m: None)
        self.assertEqual(made, ["20261122-000000-abcdef"])
        self.assertIn("주인공: Owl", calls[0][0])
        row = sources.rows(self.conn, "id = ?", (c["id"],))[0]
        self.assertEqual((row["status"], row["draft_id"]), ("drafted", "20261122-000000-abcdef"))

    def test_collect_end_to_end_with_fakes(self):
        def fetch(url, headers=None):
            if "wikipedia" in url:
                return json.dumps({"type": "standard", "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Axolotl"}}}).encode()
            return RSS

        def asker(system, prompt):
            if "언제 올려도" in system:
                return json.dumps({"items": [{"subject": "Axolotl", "wiki_title": "Axolotl", "angle": "재생"}]})
            n = prompt.count("\n") + 1
            return json.dumps({"items": [{"i": i, "curiosity": 4, "emotion": 4, "share": 4, "korea": 3, "visual": 4,
                                          "factual": 4, "subject": f"Animal{i}", "photo_query": f"animal{i}",
                                          "ko_title": f"제목{i}"} for i in range(n)]})

        def finder(q):
            return [{"page_url": f"{q}-{k}", "source": "wikimedia_commons", "title": q} for k in range(6)]
        with patch.dict(os.environ, {"NAVER_CLIENT_ID": ""}), \
             patch.object(sources, "FEEDS", [("news_intl", "A", "https://a")]), \
             patch.object(sources, "used_subjects", lambda conn: []):
            r = sources.collect(conn=self.conn, asker=asker, fetch=fetch, finder=finder, evergreen=1,
                                log=lambda m: None, pause=lambda s: None)
            r2 = sources.collect(conn=self.conn, asker=asker, fetch=fetch, finder=finder, evergreen=1,
                                 log=lambda m: None, pause=lambda s: None)
        self.assertEqual(r["new"], 3)            # 물고기·펭귄 기사 + 상시 소재 1
        self.assertEqual(r["candidates"], 3)
        self.assertEqual(r2["new"], 0)           # 다시 모아도 중복 없음
        listed = sources.list_candidates(self.conn)
        self.assertEqual(len(listed), 3)
        self.assertEqual({c["kind"] for c in listed}, {"news_intl", "evergreen"})


if __name__ == "__main__":
    unittest.main()
