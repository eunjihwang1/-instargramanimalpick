"""모음형(리스트) 원고 오프라인 테스트."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from animalpick import config, drafts, render, writer  # noqa: E402
from test_pipeline import fake_downloader  # noqa: E402

NAMES = [("오카피", "Okapi"), ("천산갑", "Pangolin"), ("아이아이", "Aye-aye"), ("블롭피시", "Blobfish"),
         ("별코두더지", "Star-nosed mole"), ("푸른갯민숭달팽이", "Glaucus atlanticus"), ("가짜 이름", "Nopeasaurus")]


def plan_json():
    return {"theme": "진짜 존재하는 '외계인 같은' 동물",
            "items": [{"name_ko": k, "wiki_title": e, "image_subject": e} for k, e in NAMES]}


def write_json(n):
    return {"topic": "외계인 같은 동물", "headline": "실제로 존재하는\\n'외계인 같은' 동물 6종",
            "headline_options": ["a", "b", "c", "d", "e"], "cover_image_query": "okapi",
            "items": [{"n": i, "name_ko": "x", "fact": f"{i}번 대상은 놀랍습니다.", "image_query": NAMES[i - 1][1]}
                      for i in range(1, n + 1)],
            "caption_intro": "지구에도 외계인처럼 생긴 동물이 있습니다.", "caption_outro": "어느 동물이 가장 놀랍습니까?",
            "hashtags": ["#신기한동물", "외계인"], "fact_notes": "확인"}


class FakeAsker:
    def __init__(self):
        self.calls = 0

    def __call__(self, system, prompt):
        self.calls += 1
        if "기획자" in system:
            return json.dumps(plan_json(), ensure_ascii=False)
        n = prompt.count("[항목 ")
        return json.dumps(write_json(n), ensure_ascii=False)


def fetcher(url):
    if "Nopeasaurus" in url:
        raise OSError("404")
    return "본문입니다."


def finder(q):
    if "Blobfish" in q:
        return []  # 사진 없는 항목
    return [{"title": f"File:{q} {k}.jpg", "download_url": f"https://x/{q}{k}.jpg", "page_url": f"{q}{k}",
             "author": f"A-{q}-{k}", "license": "CC0", "source": "wikimedia_commons"} for k in range(3)]


class ListWriterTests(unittest.TestCase):
    def test_only_wiki_checked_items_and_numbering(self):
        d = writer.write_list_draft("외계인 같은 동물", asker=FakeAsker(), fetcher=fetcher, sleep=lambda s: None)
        self.assertEqual(d["format"], "list")
        self.assertEqual(len(d["items"]), 6)  # 위키 문서 없는 'Nopeasaurus' 제외
        self.assertTrue(any("Nopeasaurus" in w or "가짜 이름" in w for w in d["warnings"]))
        self.assertIn("\n", d["slides"][0]["headline"])
        self.assertEqual(d["hashtags"], ["신기한동물", "외계인"])

    def test_too_few_items_fails(self):
        def bad_fetch(url):
            raise OSError("x")
        with self.assertRaises(RuntimeError):
            writer.write_list_draft("주제", asker=FakeAsker(), fetcher=bad_fetch, sleep=lambda s: None)

    def test_compose_caption_numbers(self):
        draft = {"caption_intro": "도입입니다.", "caption_outro": "끝입니다."}
        cap = writer.compose_list_caption(draft, [{"name_ko": "오카피", "fact": "A입니다."},
                                                  {"name_ko": "천산갑", "fact": "B입니다."}])
        self.assertIn("① 오카피: A입니다.\n② 천산갑: B입니다.", cap)
        self.assertTrue(cap.endswith("출처: Wikipedia"))


class ListDraftFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.object(config, "DRAFTS", Path(self.tmp.name) / "drafts")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def make(self):
        write = lambda t, u, s: writer.write_list_draft(t, asker=FakeAsker(), fetcher=fetcher, sleep=lambda s: None)
        return drafts.create("외계인 같은 동물", "", "", write=write, finder=finder, downloader=fake_downloader,
                             pause=lambda s: None)

    def test_items_without_photo_dropped_and_caption_matches_photos(self):
        d = self.make()
        shown = render.list_shown_items(d)
        self.assertEqual([i["name_ko"] for i in shown], ["오카피", "천산갑", "아이아이"])  # 블롭피시 사진 없음 → 5개 → 3개 단위
        self.assertEqual(len(d["slide_files"]), 2)  # 표지 + 모음 1장
        self.assertIn("① 오카피", d["caption"])
        self.assertIn("③ 아이아이", d["caption"])
        self.assertNotIn("블롭피시", d["caption"])
        self.assertNotIn("④", d["caption"])  # 사진에 없는 번호는 캡션에도 없다
        # 표지는 ①과 다른 사진
        self.assertNotEqual(d["slides"][0]["image"]["page_url"], shown[0]["image"]["page_url"])
        cap = drafts.full_caption(d)
        self.assertIn("사진:", cap)

    def test_hide_item_renumbers_caption(self):
        d = self.make()
        r = drafts.update_content(d["id"], {"items": [{"hidden": True}]})  # 오카피 빼기
        shown = render.list_shown_items(r)
        self.assertEqual(shown[0]["name_ko"], "천산갑")
        self.assertIn("① 천산갑", r["caption"])

    def test_numbers_drawn(self):
        d = self.make()
        with Image.open(drafts.draft_dir(d["id"]) / "slide_02.jpg") as im:
            px = im.getpixel((18 + 26, 18 + 8))  # 번호 배지(흰 원) 위쪽
            self.assertTrue(all(v >= 230 for v in px), px)


if __name__ == "__main__":
    unittest.main()
