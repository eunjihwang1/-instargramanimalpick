"""사진 모음(콜라주) 구성 + 주제 확장(넓은 피드·금지어·NASA·분야 상한) 오프라인 테스트."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from animalpick import config, drafts, images, render, sources, writer  # noqa: E402
from test_pipeline import AI_JSON, FakeResponse, fake_downloader  # noqa: E402


def img(i, source="wikimedia_commons"):
    return {"file": f"img_g{i:02d}.jpg", "credit": f"A{i} / CC0", "page_url": f"u{i}", "source": source,
            "author": f"A{i}", "hidden": False}


def draft_with(n_gallery, cover=True, keys=0):
    slides = [{"kind": "cover", "headline": "제목", "body": "", "image_query": "q",
               "image": {"file": "img_01.jpg", "credit": "C / CC0", "page_url": "cover", "source": "wikimedia_commons"} if cover else None}]
    for k in range(keys):
        slides.append({"kind": "key", "headline": "핵심", "image_query": "k", "must_match": ["x"],
                       "image": {"file": f"img_k{k}.jpg", "credit": "K / CC0", "page_url": f"k{k}", "source": "wikimedia_commons"}})
    return {"slides": slides, "gallery": [img(i) for i in range(1, n_gallery + 1)], "caption": "첫 줄입니다.\n\n둘째입니다."}


class PlanTests(unittest.TestCase):
    def test_pages_by_photo_count(self):
        kinds = lambda d: [k for k, _ in render.plan_pages(d)]
        self.assertEqual(kinds(draft_with(0)), ["cover", "photo"])             # 사진 모음 없음 → 예전 2장
        self.assertEqual(kinds(draft_with(2)), ["cover", "photo"])             # 3장 미만 → 예전 2장
        self.assertEqual(kinds(draft_with(3)), ["cover", "collage"])
        self.assertEqual(kinds(draft_with(7)), ["cover", "collage", "collage"])  # 3장 단위로 자름
        self.assertEqual(kinds(draft_with(12)), ["cover", "collage", "collage", "collage"])  # 최대 9장
        self.assertEqual(kinds(draft_with(0, cover=False)), ["cover", "text"])

    def test_key_photo_first_in_collage_and_hidden_skipped(self):
        d = draft_with(3, keys=1)
        d["gallery"][0]["hidden"] = True
        photos = render.collage_photos(d)
        self.assertEqual([p["page_url"] for p in photos], ["k0", "u2", "u3"])

    def test_used_images_for_credits(self):
        d = draft_with(4)
        used = render.used_images(d)
        self.assertEqual([u["page_url"] for u in used], ["cover", "u1", "u2", "u3"])  # 4번째는 3장 단위에서 빠짐
        self.assertEqual(len(drafts.full_caption({**d, "hashtags": []}).split("사진: ")[1].split(",")), 4)

    def test_render_collage_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            d = draft_with(6)
            for name in ["img_01.jpg"] + [g["file"] for g in d["gallery"]]:
                fake_downloader("", out / name)
            paths = render.render_draft(d, out)
            self.assertEqual(len(paths), 3)
            for p in paths:
                with Image.open(p) as im:
                    self.assertEqual(im.size, (1080, 1350))
            with Image.open(paths[1]) as im:  # 사진 사이 흰 틈
                px = im.getpixel((540, (1350 - render.COLLAGE_GAP) // 2 + 2))
                self.assertTrue(all(v >= 240 for v in px), px)  # JPEG 압축 오차 허용


class GalleryFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.object(config, "DRAFTS", Path(self.tmp.name) / "drafts")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def finder(self, q):
        base = [{"title": f"File:Red panda {q} {i}.jpg", "download_url": f"https://x/{q}{i}.jpg",
                 "page_url": f"https://c/{q}{i}", "author": f"T-{q}-{i}", "license": "CC0", "source": "wikimedia_commons"}
                for i in range(3)]
        noise = [{"title": "File:Panda bear.jpg", "download_url": "https://x/bear.jpg", "page_url": "bear",
                  "author": "T", "license": "CC0", "source": "wikimedia_commons"},
                 {"title": "red panda, fantasy, art", "download_url": "https://x/ai.jpg", "page_url": "ai",
                  "author": "S", "license": "Pixabay Content License", "source": "pixabay"}]
        return base + noise

    def test_gallery_filled_unique_and_filtered(self):
        data = {**AI_JSON, "image_subject": "red panda", "gallery_queries": ["red panda eating", "red panda cub"]}
        write = lambda t, u, s: writer.normalize(data, has_source=True)
        d = drafts.create("판다", "", "", write=write, finder=self.finder, downloader=fake_downloader, pause=lambda s: None)
        urls = [g["page_url"] for g in d["gallery"]]
        self.assertEqual(len(urls), 8)  # 검색어 3개 × 3장 = 9, 그중 1장은 표지
        self.assertEqual(len(set(urls)), len(urls))
        self.assertNotIn("bear", urls)   # 'Panda bear' 는 red panda 가 아님
        self.assertNotIn("ai", urls)     # AI 그림 태그
        self.assertNotIn(d["slides"][0]["image"]["page_url"], urls)  # 표지와 겹치지 않음
        self.assertEqual(len(d["slide_files"]), 3)  # 표지 + 모음 2장 (6장, 3장 단위)

    def test_same_photo_under_other_address_skipped(self):
        def finder(q):
            return [{"title": f"File:Red panda {k}.jpg", "download_url": url, "page_url": f"p{k}",
                     "author": "T", "license": "CC0", "source": "wikimedia_commons"}
                    for k, url in enumerate(["https://x/a.jpg", "https://x/b.jpg", "https://x/a.jpg",
                                             "https://x/c.jpg", "https://x/d.jpg"])]
        data = {**AI_JSON, "image_subject": "red panda", "gallery_queries": []}
        write = lambda t, u, s: writer.normalize(data, has_source=True)
        d = drafts.create("판다3", "", "", write=write, finder=finder, downloader=fake_downloader, pause=lambda s: None)
        urls = [d["slides"][0]["image"]["download_url"]] + [g["download_url"] for g in d["gallery"]]
        self.assertEqual(len(urls), len(set(urls)))  # 같은 그림(a.jpg)이 두 번 들어가지 않음
        self.assertTrue(any("같은 사진" in w for w in d["warnings"]))

    def test_author_cap_against_burst_shots(self):
        def finder(q):
            stock = [{"title": f"red panda, branch {k}", "download_url": f"https://x/s{k}.jpg", "page_url": f"s{k}",
                      "author": "Pexels", "license": "Pixabay Content License", "source": "pixabay"} for k in range(3)]
            commons = [{"title": f"File:Red Panda ({k}).jpg", "download_url": f"https://x/c{k}.jpg", "page_url": f"c{k}",
                        "author": "Mathias Appel", "license": "CC0", "source": "wikimedia_commons"} for k in range(4)]
            return commons + stock
        data = {**AI_JSON, "image_subject": "red panda", "gallery_queries": []}
        write = lambda t, u, s: writer.normalize(data, has_source=True)
        d = drafts.create("판다4", "", "", write=write, finder=finder, downloader=fake_downloader, pause=lambda s: None)
        authors = [d["slides"][0]["image"]["author"]] + [g["author"] for g in d["gallery"]]
        self.assertEqual(authors.count("Mathias Appel"), 2)  # 표지 포함 위키미디어 같은 사진가 2장까지
        self.assertEqual(authors.count("Pexels"), 1)          # 사진 사이트 같은 사진가 1장까지

    def test_cover_failed_promotes_gallery_photo(self):
        def downloader(url, dest, fallback_url=None):
            if url.endswith("red panda0.jpg") or url.endswith("red panda1.jpg") or url.endswith("red panda2.jpg"):
                raise RuntimeError("429")  # 표지 검색어(red panda) 결과는 전부 실패
            return fake_downloader(url, dest)
        data = {**AI_JSON, "image_subject": "red panda", "gallery_queries": ["red panda eating", "red panda cub"]}
        write = lambda t, u, s: writer.normalize(data, has_source=True)
        d = drafts.create("판다5", "", "", write=write, finder=self.finder, downloader=downloader, pause=lambda s: None)
        self.assertIsNotNone(d["slides"][0]["image"])
        self.assertTrue(any("첫 사진을 표지로" in w for w in d["warnings"]))
        self.assertNotIn(d["slides"][0]["image"]["page_url"], [g["page_url"] for g in d["gallery"]])

    def test_dull_photos_excluded(self):
        self.assertFalse(images.matches_photo(
            {"source": "nasa", "title": "A lithium extraction facility in Bolivia's Salar de Uyuni"}, "Salar de Uyuni"))
        self.assertTrue(images.matches_photo(
            {"source": "wikimedia_commons", "title": "File:Salar de Uyuni reflection.jpg"}, "Salar de Uyuni"))

    def test_gallery_hide_and_restore(self):
        data = {**AI_JSON, "image_subject": "red panda", "gallery_queries": ["red panda eating"]}
        write = lambda t, u, s: writer.normalize(data, has_source=True)
        d = drafts.create("판다2", "", "", write=write, finder=self.finder, downloader=fake_downloader, pause=lambda s: None)
        n = len(d["gallery"])
        r = drafts.update_content(d["id"], {"gallery": [{"hidden": True}] * (n - 1)})
        self.assertEqual(len(render.collage_photos(r)), 0)       # 남은 1장 → 3장 미만이라 모음 없음
        self.assertEqual(len(r["slide_files"]), 2)               # 표지 + 같은 사진
        r2 = drafts.update_content(d["id"], {"gallery": [{"hidden": False}] * n})
        self.assertEqual(len(r2["slide_files"]), 1 + len(render.collage_photos(r2)) // 3)
        self.assertGreaterEqual(len(r2["slide_files"]), 2)


class BroadSourcingTests(unittest.TestCase):
    def test_blocked_titles(self):
        for t in ("Man murdered in park", "Election results", "Stocks fall sharply", "남성 583명과 성관계",
                  "대통령 순방", "총격 사건"):
            self.assertTrue(sources.blocked(t), t)
        for t in ("Gravity sends messages for the first time", "Saturn's rings are vanishing", "호주 오리 패션쇼"):
            self.assertFalse(sources.blocked(t), t)

    def test_broad_feed_no_animal_filter_and_cap(self):
        rss = "<rss><channel>" + "".join(
            f"<item><title>Robot story {i}</title><link>https://ex.com/r{i}</link></item>" for i in range(30)) + \
            "<item><title>Election chaos</title><link>https://ex.com/e</link></item></channel></rss>"
        with patch.object(sources, "FEEDS", [("news_intl", "B", "https://b", "broad"), ("news_intl", "A", "https://a", "animal")]):
            got = sources.collect_feeds(fetch=lambda url, headers=None: rss.encode(), log=lambda m: None)
        self.assertEqual(sum(1 for g in got if g["source"] == "B"), sources.BROAD_CAP)  # 동물 아니어도 통과, 상한
        self.assertEqual(sum(1 for g in got if g["source"] == "A"), 0)                 # 동물 피드는 동물만
        self.assertFalse(any("Election" in g["title"] for g in got))

    def test_category_cap_in_daily_pick(self):
        names = ["Owl", "Bear", "Fox", "Crab", "Seal", "Eel"]
        c = [{"id": str(i), "kind": "news_intl", "status": "new", "photos": 5, "score": 30 - i,
              "subject": names[i], "category": "animal"} for i in range(6)]
        c.append({"id": "s", "kind": "news_intl", "status": "new", "photos": 5, "score": 10, "subject": "Saturn",
                  "category": "space"})
        picks = sources.pick_daily(c, {"news_intl": 6})
        self.assertEqual(sum(1 for p in picks if p["category"] == "animal"), sources.CATEGORY_CAP)
        self.assertIn("Saturn", [p["subject"] for p in picks])


class NasaTests(unittest.TestCase):
    def test_parse_and_skip_copyright(self):
        payload = {"collection": {"items": [
            {"data": [{"title": "Aurora from ISS", "nasa_id": "iss001", "center": "JSC", "keywords": ["aurora"]}],
             "links": [{"rel": "preview", "href": "https://images-assets.nasa.gov/image/iss001/iss001~thumb.jpg"}]},
            {"data": [{"title": "Aurora photo", "description": "Copyright John Doe", "nasa_id": "x2"}],
             "links": [{"rel": "preview", "href": "https://images-assets.nasa.gov/image/x2/x2~thumb.jpg"}]},
        ]}}
        res = images.search_nasa("aurora", opener=lambda req, timeout: FakeResponse(json.dumps(payload).encode()))
        self.assertEqual(len(res), 1)
        r = res[0]
        self.assertEqual(r["download_url"], "https://images-assets.nasa.gov/image/iss001/iss001~large.jpg")
        self.assertEqual(r["original_url"], "https://images-assets.nasa.gov/image/iss001/iss001~orig.jpg")
        self.assertEqual(r["source"], "nasa")
        self.assertEqual(images.credit_line(r), "NASA/JSC")
        self.assertTrue(images.matches_photo(r, "aurora"))


if __name__ == "__main__":
    unittest.main()
