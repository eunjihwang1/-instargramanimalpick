"""캐러셀 이미지 렌더러 (Pillow). 1080×1350 (4:5) JPEG.

디자인: 사진 중심의 깔끔한 매거진 톤. 장식 최소화, 브랜드 표기는 'ANIMAL PICK' 한 곳.
- cover : 사진 전체 + 아래쪽 어두운 그라데이션 + 큰 흰 제목
- body  : 위 사진(60%) + 아래 흰 바탕 제목/본문
- end   : 밝은 바탕 정리 + 출처·사진 저작자 표기
사진이 없으면 같은 레이아웃을 단색 배경으로 그린다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .images import credits_by_source

W, H = 1080, 1350
PHOTO_H = 790
MARGIN = 72
INK = (17, 17, 17)
SUB = (70, 70, 70)
PAPER = (247, 244, 238)
ACCENT = (255, 196, 0)
BRAND = "ANIMAL PICK"

FONT_DIRS = [Path("C:/Windows/Fonts"), Path.home() / "AppData/Local/Microsoft/Windows/Fonts"]


@lru_cache(maxsize=None)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for d in FONT_DIRS:
        vf = d / "NotoSansKR-VF.ttf"
        if vf.exists():
            f = ImageFont.truetype(str(vf), size)
            try:
                f.set_variation_by_axes([800 if bold else 450])
            except Exception:
                pass
            return f
    for d in FONT_DIRS:
        p = d / ("malgunbd.ttf" if bold else "malgun.ttf")
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def wrap(text: str, fnt, max_width: int) -> list[str]:
    """공백 단위로 줄바꿈, 한 단어가 너무 길면 글자 단위로 자른다. \\n 은 강제 줄바꿈."""
    lines: list[str] = []
    for para in text.split("\n"):
        words, line = para.split(" "), ""
        for word in words:
            trial = f"{line} {word}".strip()
            if fnt.getlength(trial) <= max_width:
                line = trial
                continue
            if line:
                lines.append(line)
            line = ""
            for ch in word:
                if fnt.getlength(line + ch) > max_width and line:
                    lines.append(line)
                    line = ch
                else:
                    line += ch
        lines.append(line)
    return [l for l in lines] or [""]


def draw_text(draw, xy, text, fnt, fill, max_width, spacing=1.35) -> int:
    """여러 줄 그리고 다음 y 반환."""
    x, y = xy
    step = int(fnt.size * spacing)
    for line in wrap(text, fnt, max_width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += step
    return y


def fit_font(text, max_width, max_lines, start, minimum, bold=True):
    size = start
    while size > minimum:
        if len(wrap(text, font(size, bold), max_width)) <= max_lines:
            break
        size -= 4
    return font(size, bold)


def load_photo(path: str | None, size: tuple[int, int]) -> Image.Image | None:
    if not path or not Path(path).exists():
        return None
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.45))


def credit(draw, img_box_bottom: int, text: str):
    if not text:
        return
    f = font(22)
    w = f.getlength(text)
    x, y = W - MARGIN // 2 - w, img_box_bottom - 40
    draw.text((x + 1, y + 1), text, font=f, fill=(0, 0, 0))
    draw.text((x, y), text, font=f, fill=(235, 235, 235))


def gradient(height: int, max_alpha: int = 230) -> Image.Image:
    g = Image.new("L", (1, height))
    for y in range(height):
        g.putpixel((0, y), int(max_alpha * (y / height) ** 1.6))
    return g.resize((W, height))


def render_cover(slide: dict, photo: str | None, credit_text: str) -> Image.Image:
    img = load_photo(photo, (W, H))
    canvas = img if img else Image.new("RGB", (W, H), (28, 28, 30))
    shade = Image.new("RGB", (W, 820), (0, 0, 0))
    canvas.paste(shade, (0, H - 820), gradient(820))
    d = ImageDraw.Draw(canvas)
    d.text((MARGIN, 64), BRAND, font=font(30, True), fill=(255, 255, 255))
    title_font = fit_font(slide["headline"], W - MARGIN * 2, 3, 104, 64)
    lines = wrap(slide["headline"], title_font, W - MARGIN * 2)
    step = int(title_font.size * 1.22)
    y = H - 150 - step * len(lines)
    d.rectangle([MARGIN, y - 40, MARGIN + 64, y - 30], fill=ACCENT)
    for line in lines:
        d.text((MARGIN, y), line, font=title_font, fill=(255, 255, 255))
        y += step
    if slide.get("body"):
        d.text((MARGIN, H - 120), slide["body"][:40], font=font(32), fill=(225, 225, 225))
    credit(d, 40 + 40, credit_text)  # 표지는 오른쪽 위에 작게
    return canvas


def render_text_only(slide: dict, page: str) -> Image.Image:
    """사진이 없는 장: 밝은 종이 바탕에 글자를 세로 가운데로."""
    canvas = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(canvas)
    d.text((MARGIN, 64), BRAND, font=font(28, True), fill=INK)
    head = fit_font(slide["headline"], W - MARGIN * 2, 3, 92, 52)
    body_f = font(46)
    head_lines = wrap(slide["headline"], head, W - MARGIN * 2)
    body_lines = wrap(slide.get("body", ""), body_f, W - MARGIN * 2) if slide.get("body") else []
    total = int(head.size * 1.25) * len(head_lines) + 40 + int(body_f.size * 1.55) * len(body_lines)
    y = max(200, (H - total) // 2)
    d.rectangle([MARGIN, y - 50, MARGIN + 64, y - 40], fill=ACCENT)
    y = draw_text(d, (MARGIN, y), slide["headline"], head, INK, W - MARGIN * 2, 1.25)
    draw_text(d, (MARGIN, y + 40), slide.get("body", ""), body_f, SUB, W - MARGIN * 2, 1.55)
    d.text((W - MARGIN - font(24).getlength(page), H - 64), page, font=font(24), fill=(150, 150, 150))
    return canvas


def render_body(slide: dict, photo: str | None, credit_text: str, page: str) -> Image.Image:
    img = load_photo(photo, (W, PHOTO_H))
    if not img:
        return render_text_only(slide, page)
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    canvas.paste(img, (0, 0))
    d = ImageDraw.Draw(canvas)
    credit(d, PHOTO_H, credit_text)
    head = fit_font(slide["headline"], W - MARGIN * 2, 2, 62, 44)
    y = draw_text(d, (MARGIN, PHOTO_H + 56), slide["headline"], head, INK, W - MARGIN * 2, 1.25)
    draw_text(d, (MARGIN, y + 18), slide.get("body", ""), font(38), SUB, W - MARGIN * 2, 1.5)
    d.text((W - MARGIN - font(24).getlength(page), H - 64), page, font=font(24), fill=(150, 150, 150))
    d.text((MARGIN, H - 64), BRAND, font=font(22, True), fill=(150, 150, 150))
    return canvas


def render_end(slide: dict, page: str, footer_lines: list[str]) -> Image.Image:
    canvas = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(canvas)
    d.text((MARGIN, 64), BRAND, font=font(30, True), fill=INK)
    d.rectangle([MARGIN, 330, MARGIN + 64, 340], fill=ACCENT)
    head = fit_font(slide["headline"], W - MARGIN * 2, 3, 76, 48)
    y = draw_text(d, (MARGIN, 380), slide["headline"], head, INK, W - MARGIN * 2, 1.25)
    draw_text(d, (MARGIN, y + 24), slide.get("body", ""), font(42), SUB, W - MARGIN * 2, 1.5)
    small = font(24)
    wrapped = [l for line in footer_lines for l in wrap(line, small, W - MARGIN * 2 - 80)]
    fy = H - 110 - int(small.size * 1.4) * len(wrapped)
    for line in wrapped:
        d.text((MARGIN, fy), line, font=small, fill=(120, 120, 120))
        fy += int(small.size * 1.4)
    d.text((W - MARGIN - font(24).getlength(page), H - 64), page, font=font(24), fill=(150, 150, 150))
    return canvas


def footer_for(draft: dict) -> list[str]:
    lines = []
    srcs = [s.get("title") or s.get("url") for s in draft.get("sources", [])][:2]
    if srcs:
        lines.append("출처: " + " · ".join(x[:40] for x in srcs))
    photo_credit = credits_by_source(draft["slides"])
    if photo_credit:
        lines.append(photo_credit)
    if any(s.get("image") and s["image"].get("ai_generated") for s in draft["slides"]):
        lines.append("일부 이미지는 AI로 생성했습니다.")
    return lines


def render_photo(photo: str, credit_text: str) -> Image.Image:
    """글자 없는 사진 장: 1080×1350 꽉 채운 사진 + 오른쪽 아래 작은 저작자 표기만."""
    canvas = load_photo(photo, (W, H))
    d = ImageDraw.Draw(canvas)
    credit(d, H - 8, credit_text)
    return canvas


def caption_preview(draft: dict) -> dict:
    """사진이 1장도 없을 때 2장째를 채울 글자 장 (캡션 첫 부분)."""
    paras = [p.strip() for p in draft.get("caption", "").split("\n\n") if p.strip() and not p.startswith("출처")]
    return {"headline": paras[0] if paras else draft["slides"][0]["headline"],
            "body": " ".join(paras[1:3])}


COLLAGE_GAP = 6


def render_collage(photos: list[tuple[str, str]]) -> Image.Image:
    """관련 사진 3장 모음 (2026-11-23 사용자 레퍼런스): 위 큰 사진 1 + 아래 작은 사진 2, 글자 없음.
    photos = [(파일 경로, 저작자 표기)] 3개. 칸마다 오른쪽 아래 작은 저작자 표기."""
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    top_h = (H - COLLAGE_GAP) // 2
    half_w = (W - COLLAGE_GAP) // 2
    boxes = [(0, 0, W, top_h),
             (0, top_h + COLLAGE_GAP, half_w, H),
             (half_w + COLLAGE_GAP, top_h + COLLAGE_GAP, W, H)]
    d = ImageDraw.Draw(canvas)
    f = font(18)
    for (path, credit_text), (x0, y0, x1, y1) in zip(photos, boxes):
        img = load_photo(path, (x1 - x0, y1 - y0))
        if img:
            canvas.paste(img, (x0, y0))
        if credit_text:
            w = f.getlength(credit_text)
            tx, ty = max(x0 + 8, x1 - 12 - w), y1 - 30
            d.text((tx + 1, ty + 1), credit_text, font=f, fill=(0, 0, 0))
            d.text((tx, ty), credit_text, font=f, fill=(235, 235, 235))
    return canvas


def collage_photos(draft: dict) -> list[dict]:
    """사진 모음에 들어갈 사진: 핵심 장면 사진 먼저, 그다음 사진 모음(뺀 것 제외). 3장 단위로 자른다."""
    keys = [s["image"] for s in draft["slides"][1:] if s.get("kind") == "key" and s.get("image")]
    extra = [g for g in draft.get("gallery") or [] if not g.get("hidden")]
    photos = (keys + extra)[:9]
    return photos[: len(photos) // 3 * 3]


def plan_pages(draft: dict) -> list[tuple]:
    """1장 표지(사진+제목) → 2장부터 관련 사진 3장 모음(최대 3장).
    관련 사진이 3장이 안 되면 예전 방식: 표지 사진(제목 없이) + 핵심 사진을 한 장씩.
    표지 사진이 없으면 2장째를 캡션 요약 글자 장으로 채운다 (캐러셀은 최소 2장)."""
    cover = draft["slides"][0]
    photos = collage_photos(draft)
    if photos:
        return [("cover", cover)] + [("collage", photos[i:i + 3]) for i in range(0, len(photos), 3)]
    keys = [("photo", s) for s in draft["slides"][1:] if s.get("kind") == "key" and s.get("image")]
    if cover.get("image"):
        return ([("cover", cover), ("photo", cover)] + keys)[:10]
    return ([("cover", cover)] + (keys or [("text", caption_preview(draft))]))[:10]


def used_images(draft: dict) -> list[dict]:
    """실제 캐러셀에 들어가는 사진 정보 (캡션 저작자 표기·AI 확인 경고에 쓴다)."""
    out = []
    for kind, item in plan_pages(draft):
        if kind == "collage":
            out.extend(item)
        elif kind in ("cover", "photo") and item.get("image"):
            out.append(item["image"])
    seen, uniq = set(), []
    for i in out:
        key = i.get("page_url") or i.get("file")
        if key not in seen:
            seen.add(key)
            uniq.append(i)
    return uniq


def render_draft(draft: dict, out_dir: Path) -> list[Path]:
    """plan_pages 순서대로 slide_01.jpg… 로 저장하고 경로 목록 반환."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("slide_*.jpg"):
        old.unlink()
    pages = plan_pages(draft)
    paths = []
    for i, (kind, slide) in enumerate(pages, 1):
        if kind == "collage":
            img = render_collage([(str(out_dir / p["file"]), p.get("credit", "")) for p in slide])
            path = out_dir / f"slide_{i:02d}.jpg"
            img.convert("RGB").save(path, "JPEG", quality=92, optimize=True)
            paths.append(path)
            continue
        image = slide.get("image") or {}
        photo = str(out_dir / image["file"]) if image.get("file") else None
        credit_text = image.get("credit", "")
        if kind == "cover":
            img = render_cover({**slide, "body": ""}, photo, credit_text)
        elif kind == "photo":
            img = render_photo(photo, credit_text)
        else:
            img = render_text_only(slide, f"{i}/{len(pages)}")
        path = out_dir / f"slide_{i:02d}.jpg"
        img.convert("RGB").save(path, "JPEG", quality=92, optimize=True)
        paths.append(path)
    return paths
