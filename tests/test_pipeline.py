"""제작 → 검수 → 게시 파이프라인 오프라인 테스트 (네트워크·AI·Meta 호출 없음)."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from animalpick import config, drafts, graph, images, publisher, render, token_setup, writer  # noqa: E402

AI_JSON = {
    "topic": "레서판다 탈출",
    "fact_status": "sourced",
    "slides": [
        {"kind": "cover", "headline": "동물원 탈출한 레서판다,\\n사흘 만에 돌아온 곳", "body": "", "image_query": "red panda"},
        {"kind": "body", "headline": "사라진 건 월요일", "body": "우리는 비어 있었다.", "image_query": "red panda"},
        {"kind": "body", "headline": "사흘간의 수색", "body": "드론까지 동원됐다.", "image_query": "drone"},
        {"kind": "end", "headline": "결국 집으로", "body": "끝.", "image_query": ""},
    ],
    "caption": "레서판다가 돌아왔다\n출처: 테스트",
    "hashtags": ["#레서판다", "동물원 소식", ""],
    "sources": [],
    "fact_notes": "날짜 확인",
}


class FakeResponse(io.BytesIO):
    headers = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_asker(system, prompt):
    return "설명입니다\n```json\n" + json.dumps(AI_JSON, ensure_ascii=False) + "\n```"


def fake_finder(query):
    return [{"title": f"File:{query}{i}.jpg", "download_url": f"https://x/{query}{i}.jpg",
             "page_url": f"https://commons/{query}{i}", "author": "Tester", "license": "CC0",
             "license_url": "", "source": "wikimedia_commons", "ai_generated": False} for i in range(2)]


def fake_downloader(url, dest, fallback_url=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1600, 1200), (120, 90, 60)).save(dest, "JPEG")
    return dest


def fake_hoster(events=None):
    from contextlib import contextmanager
    events = events if events is not None else []

    @contextmanager
    def hoster(files, draft_id):
        events.append("open")
        try:
            yield [f"https://t.example/{f.name}" for f in files]
        finally:
            events.append("close")
    return hoster


class TempDataMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.object(config, "DRAFTS", Path(self.tmp.name) / "drafts")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def make(self, topic="레서판다 탈출", url="https://news.example/1"):
        write = lambda t, u, s: writer.write_draft(t, u, s, asker=fake_asker, fetcher=lambda _u: "기사 본문")
        return drafts.create(topic, url, "", write=write, finder=fake_finder, downloader=fake_downloader,
                             pause=lambda s: None)


class WriterTests(unittest.TestCase):
    def test_normalize_kinds_and_tags(self):
        d = writer.normalize(AI_JSON, source_url="https://a", has_source=True)
        self.assertEqual([s["kind"] for s in d["slides"]], ["cover"])  # 표지 하나만 (설명은 캡션)
        self.assertIn("\n", d["slides"][0]["headline"])
        self.assertEqual(d["hashtags"], ["레서판다", "동물원소식"])
        self.assertEqual(d["sources"][0]["url"], "https://a")
        self.assertEqual(d["fact_status"], "sourced")

    def test_no_source_is_unverified(self):
        d = writer.normalize(AI_JSON, has_source=False)
        self.assertEqual(d["fact_status"], "unverified")

    def test_no_cover_rejected(self):
        with self.assertRaises(ValueError):
            writer.normalize({"slides": [{"headline": ""}]})

    def test_prompt_requires_formal_style_and_caption_story(self):
        self.assertIn("합니다체", writer.SYSTEM)
        self.assertIn("캐러셀 기본은 2장", writer.SYSTEM)

    def test_fetch_failure_still_writes_unverified(self):
        def boom(_u):
            raise OSError("blocked")
        d = writer.write_draft("주제", "https://blocked", "", asker=fake_asker, fetcher=boom)
        self.assertEqual(d["fact_status"], "unverified")

    def test_fetch_failure_retried_and_warned(self):
        calls = []

        def flaky(_u):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("timeout")
            return "본문"
        d = writer.write_draft("주제", "https://x", "", asker=fake_asker, fetcher=flaky, sleep=lambda s: None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(d["fact_status"], "sourced")

        def boom(_u):
            raise OSError("blocked")
        d2 = writer.write_draft("주제", "https://x", "", asker=fake_asker, fetcher=boom, sleep=lambda s: None)
        self.assertTrue(any("출처 페이지를 읽지 못했습니다" in w for w in d2["warnings"]))

    def test_style_issues(self):
        cap = "무엇일까요?\n\n답은 돌입니다. 펭귄은 돌을 모은다.\n\n정말 놀랍습니까?\n\n출처: 위키백과"
        bad = writer.style_issues(cap)
        self.assertEqual(len(bad), 2)  # '-까요' 와 '-다' 만 (니다·니까·출처 줄은 통과)

    def test_wikipedia_uses_api_extract(self):
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return FakeResponse(json.dumps({"query": {"pages": [{"title": "Common wombat", "extract": "Wombats produce cube-shaped faeces."}]}}).encode())
        t = writer.fetch_source_text("https://en.wikipedia.org/wiki/Common_wombat", opener)
        self.assertIn("cube-shaped", t)
        self.assertIn("action=query", seen[0])
        self.assertIn("Common+wombat", seen[0])

    def test_html_to_text(self):
        t = writer.html_to_text("<html><title>제목</title><script>x()</script><p>본문 &amp; 내용</p></html>")
        self.assertIn("제목", t)
        self.assertIn("본문 & 내용", t)
        self.assertNotIn("x()", t)


class ImageTests(unittest.TestCase):
    def test_license_filter(self):
        for ok in ("CC0", "Public domain", "CC BY 4.0", "CC BY 2.0", "cc-by-3.0"):
            self.assertTrue(images.license_ok(ok), ok)
        for bad in ("CC BY-SA 4.0", "CC BY-NC 2.0", "CC BY-ND 3.0", "", "Fair use", "GFDL"):
            self.assertFalse(images.license_ok(bad), bad)

    def test_search_filters(self):
        payload = {"query": {"pages": {
            "1": {"index": 1, "title": "File:a.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 2000, "url": "u1",
                  "thumburl": "t1", "descriptionurl": "d1",
                  "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}, "Artist": {"value": "<a>Kim</a>"}}}]},
            "2": {"index": 2, "title": "File:b.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 2000,
                  "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}}}]},
            "3": {"index": 3, "title": "File:c.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 300,
                  "extmetadata": {"LicenseShortName": {"value": "CC0"}}}]},
        }}}
        res = images.search_commons("x", opener=lambda req, timeout: FakeResponse(json.dumps(payload).encode()))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["author"], "Kim")
        self.assertEqual(res[0]["download_url"], "t1")


class DownloadTests(unittest.TestCase):
    def _jpeg(self):
        buf = io.BytesIO()
        Image.new("RGB", (3000, 2000), (10, 20, 30)).save(buf, "JPEG")
        return buf.getvalue()

    def test_429_falls_back_to_original_and_shrinks(self):
        seen = []
        data = self._jpeg()

        def opener(req, timeout):
            seen.append(req.full_url)
            if "thumb" in req.full_url:
                raise HTTPError(req.full_url, 429, "too many", {"retry-after": "1"}, io.BytesIO(b""))
            return FakeResponse(data)
        with tempfile.TemporaryDirectory() as tmp:
            out = images.download("https://x/thumb.jpg", Path(tmp) / "a.jpg", opener,
                                  fallback_url="https://x/orig.jpg", sleep=lambda s: None)
            with Image.open(out) as im:
                self.assertEqual(max(im.size), 2000)
        self.assertEqual(seen, ["https://x/thumb.jpg", "https://x/thumb.jpg", "https://x/orig.jpg"])

    def test_all_429_gives_clear_error(self):
        def opener(req, timeout):
            raise HTTPError(req.full_url, 429, "too many", {}, io.BytesIO(b""))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(RuntimeError) as ctx:
            images.download("https://x/t.jpg", Path(tmp) / "a.jpg", opener, fallback_url="https://x/o.jpg",
                            sleep=lambda s: None)
        self.assertIn("429", str(ctx.exception))

    def test_other_http_error_raises(self):
        def opener(req, timeout):
            raise HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b""))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(HTTPError):
            images.download("https://x/t.jpg", Path(tmp) / "a.jpg", opener, fallback_url="o", sleep=lambda s: None)


PEXELS_PAYLOAD = {"photos": [
    {"id": 1, "width": 4000, "height": 6000, "url": "https://www.pexels.com/photo/1/", "photographer": "Kim",
     "photographer_url": "https://www.pexels.com/@kim", "alt": "red panda",
     "src": {"original": "https://images.pexels.com/photos/1/a.jpeg", "large2x": "https://images.pexels.com/photos/1/a.jpeg?w=940"}},
    {"id": 2, "width": 500, "height": 400, "url": "u2", "photographer": "Lee",
     "src": {"original": "https://images.pexels.com/photos/2/b.jpeg"}},  # 너무 작음 → 제외
]}


class PexelsTests(unittest.TestCase):
    def test_no_key_means_no_call(self):
        called = []
        with patch.dict(os.environ, {"PEXELS_API_KEY": ""}):
            self.assertEqual(images.search_pexels("x", opener=lambda *a, **k: called.append(1)), [])
        self.assertEqual(called, [])

    def test_search_parses_and_sends_raw_key(self):
        seen = []

        def opener(req, timeout):
            seen.append(req)
            return FakeResponse(json.dumps(PEXELS_PAYLOAD).encode())
        with patch.dict(os.environ, {"PEXELS_API_KEY": "PXKEY"}):
            res = images.search_pexels("red panda", opener=opener)
        self.assertEqual(seen[0].get_header("Authorization"), "PXKEY")  # Bearer 없이
        self.assertNotIn("PXKEY", seen[0].full_url)
        self.assertEqual(len(res), 1)
        r = res[0]
        self.assertEqual(r["source"], "pexels")
        self.assertEqual(r["download_url"], "https://images.pexels.com/photos/1/a.jpeg?auto=compress&cs=tinysrgb&h=2000")
        self.assertEqual(images.credit_line(r), "Kim / Pexels")

    def test_bad_key_message(self):
        def opener(req, timeout):
            raise HTTPError(req.full_url, 401, "unauth", {}, io.BytesIO(b""))
        with patch.dict(os.environ, {"PEXELS_API_KEY": "bad"}), self.assertRaises(RuntimeError) as ctx:
            images.search_pexels("x", opener=opener)
        self.assertIn("PEXELS_API_KEY", str(ctx.exception))

    def test_combined_order_and_partial_failure(self):
        c = [{"page_url": "c1", "source": "wikimedia_commons"}]
        p = [{"page_url": "p1", "source": "pexels"}]

        def boom(q):
            raise RuntimeError("429")
        self.assertEqual(images.search("x", commons=lambda q: c, pexels=lambda q: p), c + p)
        self.assertEqual(images.search("x", commons=boom, pexels=lambda q: p), p)
        with self.assertRaises(RuntimeError):
            images.search("x", commons=boom, pexels=lambda q: [])

    def test_credits_grouped_by_source(self):
        slides = [
            {"image": {"credit": "A / CC0", "author": "A", "source": "wikimedia_commons"}},
            {"image": {"credit": "Kim / Pexels", "author": "Kim", "source": "pexels"}},
            {"image": {"credit": "A / CC0", "author": "A", "source": "wikimedia_commons"}},
            {"image": None},
        ]
        self.assertEqual(images.credits_by_source(slides), "사진: A / CC0 (Wikimedia Commons) · Kim (Pexels)")


PIXABAY_PAYLOAD = {"total": 2, "totalHits": 2, "hits": [
    {"id": 10, "pageURL": "https://pixabay.com/photos/a-10/", "largeImageURL": "https://pixabay.com/get/a_1280.jpg",
     "imageWidth": 4000, "imageHeight": 3000, "user": "Park", "tags": "red panda, animal"},
    {"id": 11, "pageURL": "https://pixabay.com/photos/b-11/", "largeImageURL": "https://pixabay.com/get/b_1280.jpg",
     "imageWidth": 3000, "imageHeight": 4500, "user": "Choi", "tags": "red panda"},
]}


class PixabayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def opener_recording(self, seen, payload=PIXABAY_PAYLOAD):
        def opener(req, timeout):
            seen.append(req.full_url)
            return FakeResponse(json.dumps(payload).encode())
        return opener

    def test_no_key_no_call(self):
        seen = []
        with patch.dict(os.environ, {"PIXABAY_API_KEY": ""}):
            self.assertEqual(images.search_pixabay("x", opener=self.opener_recording(seen), cache_dir=self.cache), [])
        self.assertEqual(seen, [])

    def test_parse_portrait_first_and_credit(self):
        seen = []
        with patch.dict(os.environ, {"PIXABAY_API_KEY": "PBKEY"}):
            res = images.search_pixabay("red panda", opener=self.opener_recording(seen), cache_dir=self.cache)
        self.assertIn("safesearch=true", seen[0])
        self.assertEqual([r["author"] for r in res], ["Choi", "Park"])  # 세로 사진 먼저
        self.assertEqual(res[0]["source"], "pixabay")
        self.assertEqual(images.credit_line(res[0]), "Choi / Pixabay")

    def test_cached_for_24h(self):
        seen = []
        t = [1_000_000.0]
        with patch.dict(os.environ, {"PIXABAY_API_KEY": "PBKEY"}):
            kw = dict(opener=self.opener_recording(seen), cache_dir=self.cache, now=lambda: t[0])
            images.search_pixabay("red panda", **kw)
            cache_file = next(self.cache.glob("*.json"))
            os.utime(cache_file, (t[0], t[0]))
            images.search_pixabay("Red Panda", **kw)  # 같은 검색어 → 캐시
            self.assertEqual(len(seen), 1)
            t[0] += 25 * 3600
            images.search_pixabay("red panda", **kw)  # 24시간 지나면 다시 요청
        self.assertEqual(len(seen), 2)
        self.assertNotIn("PBKEY", cache_file.read_text(encoding="utf-8"))

    def test_error_hides_key(self):
        def opener(req, timeout):
            raise HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b""))
        with patch.dict(os.environ, {"PIXABAY_API_KEY": "PBKEY"}), self.assertRaises(RuntimeError) as ctx:
            images.search_pixabay("x", opener=opener, cache_dir=self.cache)
        self.assertNotIn("PBKEY", str(ctx.exception))
        self.assertIn("PIXABAY_API_KEY", str(ctx.exception))

    def test_combined_three_sources(self):
        c, p, x = [{"page_url": "c"}], [{"page_url": "p"}], [{"page_url": "x"}]
        self.assertEqual(images.search("q", commons=lambda q: c, pexels=lambda q: p, pixabay=lambda q: x), c + p + x)


class SubjectFilterTests(TempDataMixin, unittest.TestCase):
    def test_matches_subject(self):
        ok = {"source": "pixabay", "title": "penguins, adelie penguin, antarctica"}
        wrong = {"source": "pixabay", "title": "gentoo penguin, penguin, bird"}
        commons = {"source": "wikimedia_commons", "title": "File:Gentoo.jpg"}
        self.assertTrue(images.matches_subject(ok, "Adelie penguin"))
        self.assertFalse(images.matches_subject(wrong, "Adelie penguin"))
        self.assertTrue(images.matches_subject(commons, "Adelie penguin"))  # 위키미디어는 필터 대상 아님
        self.assertTrue(images.matches_subject(wrong, ""))  # 주인공 없으면 통과
        corpse = {"source": "wikimedia_commons", "title": "File:Adélie Penguin Corpse On Franklin Island.jpg"}
        self.assertFalse(images.matches_subject(corpse, "Adelie penguin"))
        self.assertFalse(images.matches_subject({"source": "pixabay", "title": "dead fish"}, ""))

    def test_wrong_species_stock_photos_are_dropped(self):
        def finder(q):
            return [{"title": "storks, nest", "download_url": "https://x/stork.jpg", "page_url": "s1",
                     "author": "A", "license": "Pixabay Content License", "source": "pixabay"},
                    {"title": "red panda, animal", "download_url": "https://x/rp.jpg", "page_url": "s2",
                     "author": "B", "license": "Pixabay Content License", "source": "pixabay"}]
        data = {**AI_JSON, "image_subject": "red panda"}
        write = lambda t, u, s: writer.normalize(data, has_source=True)
        d = drafts.create("필터", "", "", write=write, finder=finder, downloader=fake_downloader, pause=lambda s: None)
        used = [s["image"]["download_url"] for s in d["slides"] if s.get("image")]
        self.assertEqual(used, ["https://x/rp.jpg"])
        self.assertTrue(any("걸러냈습니다" in w for w in d["warnings"]))


KEY_JSON = {**AI_JSON, "image_subject": "Common wombat",
            "key_images": [{"scene": "네모난 똥", "image_query": "wombat scat", "must_match": ["scat", "poo", "feces"]},
                           {"scene": "확인 단어 없음", "image_query": "x", "must_match": []}]}


class KeyImageTests(TempDataMixin, unittest.TestCase):
    def test_normalize_keeps_only_checkable_key_images(self):
        d = writer.normalize(KEY_JSON, has_source=True)
        self.assertEqual([s["kind"] for s in d["slides"]], ["cover", "key"])
        self.assertEqual(d["slides"][1]["must_match"], ["scat", "poo", "feces"])

    def test_matches_key(self):
        self.assertTrue(images.matches_key({"title": "File:Wombat scat cubes.jpg"}, ["scat", "poo"], "Common wombat"))
        self.assertFalse(images.matches_key({"title": "File:Wombat sleeping.jpg"}, ["scat"], "Common wombat"))
        self.assertFalse(images.matches_key({"title": "File:Deer scat.jpg"}, ["scat"], "Common wombat"))  # 다른 동물
        self.assertFalse(images.matches_key({"title": "wombat dead scat"}, ["scat"], "Common wombat"))    # 불편한 사진
        self.assertFalse(images.matches_key({"title": "wombat scat"}, [], "Common wombat"))

    def test_ai_artwork_excluded_from_stock_only(self):
        art = {"source": "pixabay", "title": "axolotl, amphibian, animal, nature, forest, fantasy, drawing"}
        real = {"source": "pixabay", "title": "axolotl, amphibian, aquarium, pink"}
        self.assertFalse(images.matches_subject(art, "axolotl"))
        self.assertTrue(images.matches_subject(real, "axolotl"))
        self.assertFalse(images.matches_subject({"source": "pixabay", "title": "axolotl 3d model"}, "axolotl"))
        old_plate = {"source": "wikimedia_commons", "title": "File:Axolotl illustration 1880.jpg"}
        self.assertTrue(images.matches_subject(old_plate, "axolotl"))  # 위키미디어 도판은 그대로

    def test_key_prefix_and_stock_species(self):
        self.assertTrue(images.matches_key({"source": "wikimedia_commons", "title": "File:Wombat Poop Uncropped.jpg"},
                                           ["poo", "scat"], "Common wombat"))
        zoo = {"source": "pixabay", "title": "otter, stone, mammal, zoo"}
        sea = {"source": "pixabay", "title": "sea otter, otter, stone, kelp"}
        self.assertFalse(images.matches_key(zoo, ["stone"], "Sea otter"))
        place = {"source": "wikimedia_commons",
                 "title": "File:OTTER ROCK, ON THE OREGON COAST. IN THE DISTANCE, THE OPEN SEA - NARA.jpg"}
        self.assertFalse(images.matches_key(place, ["rock", "stone"], "Sea otter"))  # 지명 '오터 록'
        self.assertTrue(images.matches_key({"source": "wikimedia_commons", "title": "File:Sea otter with rock.jpg"},
                                           ["rock"], "Sea otter"))
        self.assertTrue(images.matches_key(sea, ["stone"], "Sea otter"))
        self.assertFalse(images.matches_key({"source": "wikimedia_commons", "title": "Rocket launch"}, ["rock"], ""))  # 'rock' 이 'rocket' 에 걸리면 안 됨

    def test_clean_author(self):
        self.assertEqual(images.clean_author("Unknown authorUnknown author"), "작자 미상")
        self.assertEqual(images.clean_author("Jason AuchJason Auch"), "Jason Auch")
        self.assertEqual(images.clean_author("Mathias Appel"), "Mathias Appel")

    def test_cover_prefers_commons_and_warns_on_stock(self):
        def finder(q):
            return [{"title": "red panda, animal", "download_url": "https://x/stock.jpg", "page_url": "s1",
                     "author": "S", "license": "Pixabay Content License", "source": "pixabay"},
                    {"title": "File:Red panda.jpg", "download_url": "https://x/commons.jpg", "page_url": "c1",
                     "author": "C", "license": "CC0", "source": "wikimedia_commons"}]
        write = lambda t, u, s: writer.normalize({**AI_JSON, "image_subject": "red panda"}, has_source=True)
        d = drafts.create("판다", "", "", write=write, finder=finder, downloader=fake_downloader, pause=lambda s: None)
        self.assertEqual(d["slides"][0]["image"]["download_url"], "https://x/commons.jpg")
        self.assertFalse(any("AI 로 만든" in w for w in d["warnings"]))

        only_stock = lambda q: [finder(q)[0]]
        d2 = drafts.create("판다2", "", "", write=write, finder=only_stock, downloader=fake_downloader, pause=lambda s: None)
        self.assertTrue(any("AI 로 만든" in w for w in d2["warnings"]))

    def test_key_fallback_query_with_subject_name(self):
        seen = []

        def finder(q):
            seen.append(q)
            if q == "wombat scat":
                return [{"title": "File:Wombat Faeces.jpg", "download_url": "https://x/f.jpg", "page_url": "wf",
                         "author": "C", "license": "CC0", "source": "wikimedia_commons"}]
            return [] if q.startswith("wombat cube") else fake_finder(q)
        data = {**KEY_JSON, "key_images": [{"scene": "네모난 똥", "image_query": "wombat cube poop",
                                            "must_match": ["scat", "poo", "faeces"]}]}
        write = lambda t, u, s: writer.normalize(data, has_source=True)
        d = drafts.create("웜뱃2", "", "", write=write, finder=finder, downloader=fake_downloader, pause=lambda s: None)
        self.assertEqual(d["slides"][1]["image"]["download_url"], "https://x/f.jpg")
        self.assertIn("wombat scat", seen)

    def _create(self, finder):
        write = lambda t, u, s: writer.normalize(KEY_JSON, has_source=True)
        return drafts.create("웜뱃", "", "", write=write, finder=finder, downloader=fake_downloader, pause=lambda s: None)

    def test_key_photo_found_adds_third_slide(self):
        def finder(q):
            if q == "wombat scat":
                return [{"title": "File:Wombat sleeping.jpg", "download_url": "https://x/sleep.jpg", "page_url": "k0",
                         "author": "A", "license": "CC0", "source": "wikimedia_commons"},
                        {"title": "File:Wombat scat cubes.jpg", "download_url": "https://x/scat.jpg", "page_url": "k1",
                         "author": "B", "license": "CC0", "source": "wikimedia_commons"}]
            return fake_finder(q)
        d = self._create(finder)
        self.assertEqual(d["slides"][1]["image"]["download_url"], "https://x/scat.jpg")
        self.assertEqual(d["slide_files"], ["slide_01.jpg", "slide_02.jpg", "slide_03.jpg"])

    def test_key_photo_missing_stays_two_slides(self):
        d = self._create(lambda q: [] if q.startswith("wombat") else fake_finder(q))  # 핵심 장면 사진 없음
        self.assertIsNone(d["slides"][1]["image"])
        self.assertEqual(len(d["slide_files"]), 2)
        self.assertTrue(any("핵심 사진" in w for w in d["warnings"]))


class ServerTests(unittest.TestCase):
    def test_second_server_on_same_port_fails(self):
        from animalpick import server
        first = server.make_server(0)  # 0 = 빈 포트 아무거나
        try:
            port = first.server_address[1]
            with self.assertRaises(OSError):
                server.make_server(port)
        finally:
            first.server_close()


class RenderTests(TempDataMixin, unittest.TestCase):
    def test_wrap_long_word(self):
        f = render.font(40)
        lines = render.wrap("가" * 80, f, 400)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(f.getlength(l) <= 400 for l in lines))

    def test_render_sizes(self):
        d = writer.normalize(AI_JSON, has_source=True)
        out = Path(self.tmp.name) / "r"
        fake_downloader("", out / "img_01.jpg")
        d["slides"][0]["image"] = {"file": "img_01.jpg", "credit": "Tester / CC0"}
        paths = render.render_draft(d, out)
        self.assertEqual(len(paths), 2)  # 표지 + 표지 사진
        for p in paths:
            with Image.open(p) as im:
                self.assertEqual((im.size, im.format, im.mode), ((1080, 1350), "JPEG", "RGB"))


class DraftFlowTests(TempDataMixin, unittest.TestCase):
    def test_create_attaches_unique_images(self):
        d = self.make()
        self.assertEqual(d["status"], "draft")
        self.assertEqual(d["slide_files"], ["slide_01.jpg", "slide_02.jpg"])
        self.assertEqual(len(d["slides"]), 1)
        self.assertEqual(d["slides"][0]["image"]["download_url"], "https://x/red panda0.jpg")

    def test_cover_tries_next_candidate_then_subject(self):
        def finder(q):
            return [] if q == "red panda" else fake_finder(q)  # 표지 검색어 결과 0건

        def downloader(url, dest, fallback_url=None):
            if url == "https://x/Red panda0.jpg":
                raise RuntimeError("429")
            return fake_downloader(url, dest)
        data = {**AI_JSON, "image_subject": "Red panda"}
        write = lambda t, u, s: writer.normalize(data, has_source=True)
        d = drafts.create("주제", "", "", write=write, finder=finder, downloader=downloader, pause=lambda s: None)
        # 표지 검색어 0건 → 주인공 이름으로 검색 → 첫 후보 실패 → 두 번째 후보
        self.assertEqual(d["slides"][0]["image"]["download_url"], "https://x/Red panda1.jpg")

    def test_no_cover_photo_makes_text_second_slide(self):
        write = lambda t, u, s: writer.normalize(AI_JSON, has_source=True)
        d = drafts.create("사진없음", "", "", write=write, finder=lambda q: [], downloader=fake_downloader,
                          pause=lambda s: None)
        self.assertEqual(len(d["slide_files"]), 2)  # 캐러셀 최소 2장 유지
        self.assertIsNone(d["slides"][0]["image"])

    def test_duplicate_detected(self):
        first = self.make()
        second = self.make()
        self.assertEqual(second["duplicate_of"], first["id"])

    def test_approve_requires_checklist(self):
        d = self.make()
        with self.assertRaises(ValueError):
            drafts.set_status(d["id"], "approved")
        ok = drafts.set_status(d["id"], "approved", {k: True for k in drafts.CHECKLIST})
        self.assertEqual(ok["status"], "approved")

    def test_edit_resets_to_draft(self):
        d = self.make()
        drafts.set_status(d["id"], "approved", {k: True for k in drafts.CHECKLIST})
        e = drafts.update_content(d["id"], {"slides": [{"headline": "새 제목"}], "hashtags": ["#가", "나"]})
        self.assertEqual(e["status"], "draft")
        self.assertEqual(e["slides"][0]["headline"], "새 제목")
        self.assertEqual(e["hashtags"], ["가", "나"])

    def test_removed_image_can_be_restored(self):
        d = self.make()
        before = d["slides"][0]["image"]
        r = drafts.update_content(d["id"], {"slides": [{"remove_image": True}]})
        self.assertIsNone(r["slides"][0]["image"])
        self.assertEqual(r["slides"][0]["removed_image"], before)
        self.assertTrue((drafts.draft_dir(d["id"]) / before["file"]).exists())  # 파일은 그대로
        r2 = drafts.update_content(d["id"], {"slides": [{"restore_image": True}]})
        self.assertEqual(r2["slides"][0]["image"], before)
        self.assertNotIn("removed_image", r2["slides"][0])
        self.assertEqual(len(r2["slide_files"]), len(d["slide_files"]))

    def test_publish_requires_approval(self):
        d = self.make()
        with self.assertRaises(ValueError):
            drafts.publish(d["id"], dry_run=True)

    def test_dry_run_does_not_upload_or_publish(self):
        d = self.make()
        drafts.set_status(d["id"], "approved", {k: True for k in drafts.CHECKLIST})
        called = []
        with patch.dict(os.environ, {"META_IG_USER_ID": "123"}):
            r = drafts.publish(d["id"], dry_run=True, hoster=lambda *a: called.append("host"))
        self.assertEqual(called, [])
        self.assertEqual(r["status"], "approved")
        self.assertIn("사진: Tester / CC0", r["last_dry_run"]["caption"])
        self.assertIn("#레서판다", r["last_dry_run"]["caption"])

    def test_real_publish_then_duplicate_blocked(self):
        d = self.make()
        drafts.set_status(d["id"], "approved", {k: True for k in drafts.CHECKLIST})
        fake_pub = lambda urls, caption, dry_run, log: {"media_id": "M1", "permalink": "https://ig/p/1"}
        r = drafts.publish(d["id"], dry_run=False, hoster=fake_hoster(), publish_fn=fake_pub)
        self.assertEqual(r["status"], "published")
        self.assertEqual(r["publish"]["media_id"], "M1")
        with self.assertRaises(ValueError):
            drafts.publish(d["id"], dry_run=False, publish_fn=fake_pub)

    def test_tunnel_closed_even_when_publish_fails(self):
        d = self.make()
        drafts.set_status(d["id"], "approved", {k: True for k in drafts.CHECKLIST})
        events = []

        def bad(*a, **k):
            raise publisher.PublishError("x")
        with self.assertRaises(publisher.PublishError):
            drafts.publish(d["id"], dry_run=False, hoster=fake_hoster(events), publish_fn=bad)
        self.assertEqual(events, ["open", "close"])

    def test_failed_publish_recorded(self):
        d = self.make()
        drafts.set_status(d["id"], "approved", {k: True for k in drafts.CHECKLIST})

        def bad(*a, **k):
            raise publisher.PublishError("한도 초과")
        with self.assertRaises(publisher.PublishError):
            drafts.publish(d["id"], dry_run=False, hoster=fake_hoster(), publish_fn=bad)
        self.assertEqual(drafts.load(d["id"])["status"], "failed")

    def test_run_due_only_past_approved(self):
        from datetime import datetime
        a, b = self.make("가"), self.make("나", "https://news.example/2")
        for x in (a, b):
            drafts.set_status(x["id"], "approved", {k: True for k in drafts.CHECKLIST})
        drafts.set_schedule(a["id"], "2026-01-01T09:00:00")
        drafts.set_schedule(b["id"], "2099-01-01T09:00:00")
        seen = []
        res = drafts.run_due(dry_run=False, now_fn=lambda: datetime(2026, 6, 1),
                             hoster=fake_hoster(),
                             publish_fn=lambda urls, caption, dry_run, log: seen.append(1) or {"media_id": "X"})
        self.assertEqual(len(seen), 1)
        self.assertIn(a["id"], res[0])


class FakeGraph:
    """graph.request 대체: 호출을 기록하고 정해진 응답을 돌려준다."""
    def __init__(self, status_codes=("IN_PROGRESS", "FINISHED")):
        self.calls, self.status = [], list(status_codes)
        self.n = 0

    def __call__(self, method, path, *, params=None, **kw):
        self.calls.append((method, path, dict(params or {})))
        if path.endswith("content_publishing_limit"):
            return {"data": [{"quota_usage": 2}]}
        if method == "POST" and path.endswith("/media"):
            self.n += 1
            return {"id": f"C{self.n}"}
        if method == "POST" and path.endswith("media_publish"):
            return {"id": "MEDIA1"}
        if method == "GET" and params and params.get("fields") == "status_code,status":
            return {"status_code": self.status.pop(0)}
        if path == "MEDIA1":
            return {"permalink": "https://www.instagram.com/p/xyz/"}
        return {}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"META_IG_USER_ID": "777", "META_PAGE_TOKEN": "PAGE"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_carousel_flow(self):
        fg = FakeGraph()
        with patch.object(graph, "request", fg):
            r = publisher.publish_carousel(["https://h/1.jpg", "https://h/2.jpg"], "캡션", dry_run=False,
                                           sleep=lambda s: None, log=lambda m: None)
        self.assertEqual(r["media_id"], "MEDIA1")
        posts = [c for c in fg.calls if c[0] == "POST"]
        self.assertEqual(posts[0][2], {"image_url": "https://h/1.jpg", "is_carousel_item": "true"})
        self.assertEqual(posts[2][2]["media_type"], "CAROUSEL")
        self.assertEqual(posts[2][2]["children"], "C1,C2")
        self.assertEqual(posts[3], ("POST", "777/media_publish", {"creation_id": "C3"}))

    def test_dry_run_makes_no_calls(self):
        fg = FakeGraph()
        with patch.object(graph, "request", fg):
            publisher.publish_carousel(["a", "b"], "c", dry_run=True, log=lambda m: None)
        self.assertEqual(fg.calls, [])

    def test_container_error_stops(self):
        fg = FakeGraph(status_codes=("ERROR",))
        with patch.object(graph, "request", fg), self.assertRaises(publisher.PublishError):
            publisher.publish_carousel(["a", "b"], "c", dry_run=False, sleep=lambda s: None, log=lambda m: None)
        self.assertFalse(any(c[1].endswith("media_publish") for c in fg.calls))

    def test_slide_count_limits(self):
        with self.assertRaises(publisher.PublishError):
            publisher.publish_carousel(["a"], "c")

    def test_transient_retry(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise graph.GraphError(500, 2, "-", "temporary")
            return "ok"
        self.assertEqual(publisher._retry(flaky, sleep=lambda s: None), "ok")

    def test_page_token_used(self):
        with patch.dict(os.environ, {"META_ACCESS_TOKEN": "USER"}):
            seen = []

            def opener(req, timeout):
                seen.append(req.get_header("Authorization"))
                return FakeResponse(json.dumps({"data": [{"quota_usage": 0}]}).encode())
            graph.get("777/content_publishing_limit", "quota_usage", token_env="META_PAGE_TOKEN", opener=opener)
        self.assertEqual(seen, ["Bearer PAGE"])


class TokenSetupTests(unittest.TestCase):
    def test_full_exchange_writes_env_without_printing_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("# 주석\nMETA_ACCESS_TOKEN=SHORT\nMETA_APP_ID=111\nMETA_APP_SECRET=SEC\nOTHER=keep\n",
                                encoding="utf-8")

            def opener(req, timeout):
                url = req.full_url
                if "oauth/access_token" in url:
                    body = {"access_token": "LONGUSER"}
                elif "me/accounts" in url:
                    body = {"data": [{"id": "P1", "name": "애니멀픽", "access_token": "PAGETOK",
                                      "instagram_business_account": {"id": "1784", "username": "animal..pickk"}}]}
                elif "debug_token" in url:
                    body = {"data": {"expires_at": 0}}
                else:
                    body = {}
                return FakeResponse(json.dumps(body).encode())

            keys = ("META_ACCESS_TOKEN", "META_APP_ID", "META_APP_SECRET", "META_PAGE_TOKEN", "META_IG_USER_ID",
                    "META_IG_USERNAME", "META_PAGE_ID", "OTHER")
            saved = {k: os.environ.pop(k, None) for k in keys}
            try:
                with patch.object(config, "ENV_PATH", env_path):
                    lines = token_setup.run(opener=opener)
                text = env_path.read_text(encoding="utf-8")
            finally:
                for k, v in saved.items():
                    os.environ.pop(k, None)
                    if v is not None:
                        os.environ[k] = v
        out = "\n".join(lines)
        for secret in ("LONGUSER", "PAGETOK", "SEC", "SHORT"):
            self.assertNotIn(secret, out)
        self.assertIn("만료 없음", out)
        self.assertIn("META_PAGE_TOKEN=PAGETOK", text)
        self.assertIn("META_ACCESS_TOKEN=LONGUSER", text)
        self.assertIn("META_IG_USER_ID=1784", text)
        self.assertIn("OTHER=keep", text)
        self.assertIn("# 주석", text)

    def test_unlinked_page_message(self):
        def opener(req, timeout):
            return FakeResponse(json.dumps({"data": [{"id": "P", "name": "애니멀픽", "access_token": "t"}]}).encode())
        with self.assertRaises(RuntimeError) as ctx:
            token_setup.find_linked_page("u", opener=opener)
        self.assertIn("연결된 Instagram", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
