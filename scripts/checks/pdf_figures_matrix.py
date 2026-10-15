# -*- coding: utf-8 -*-
"""Format-compatibility matrix for PdfWithFiguresParser.

Builds a synthetic PDF covering the parser's branching paths, runs the
real parser (including live VLM caption calls), and asserts the section
layout:

- page 1: colour PNG figure + text          -> text + caption expected
- page 2: JPEG figure + text                -> text + caption expected
- page 3: tiny 50x50 image (decorative)     -> text only (filtered)
- page 4: oversized 2400px image            -> text + caption (downscaled)
- page 5: text-only page                    -> text only ("无插图" filtered)
- bogus xref                                -> skipped without raising

Usage:
    python checks/pdf_figures_matrix.py
"""
import asyncio
import io
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

SERVER_DIR = Path(__file__).resolve().parent.parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from pdf_figures_parser import PdfWithFiguresParser  # noqa: E402

MAX_SIDE = 1600
failures: list[str] = []


def make_png(w, h, color, extra_draw=None):
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.ellipse([10, 10, w - 10, h - 10], fill=color)
    if extra_draw:
        extra_draw(d, w, h)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def make_jpeg(w, h, color):
    im = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    im.save(buf, format="JPEG")
    return buf.getvalue()


def build_pdf() -> bytes:
    doc = pymupdf.open()

    def add_image_page(png_bytes, label):
        page = doc.new_page()
        page.insert_text((72, 72), label, fontsize=14)
        page.insert_image(pymupdf.Rect(72, 100, 500, 500), stream=png_bytes)

    add_image_page(
        make_png(800, 600, (200, 40, 40)), "Page1: red circle PNG figure."
    )
    add_image_page(
        make_jpeg(700, 500, (40, 90, 200)), "Page2: blue JPEG figure."
    )
    add_image_page(
        make_png(50, 50, (150, 150, 150)), "Page3: tiny decorative image."
    )
    add_image_page(
        make_png(2400, 1200, (40, 160, 80)), "Page4: oversized 2400px figure."
    )
    page = doc.new_page()
    page.insert_text((72, 72), "Page5: pure text, no figures at all.", fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


async def main_async() -> int:
    parser = PdfWithFiguresParser()
    sections = await parser.parse(build_pdf(), "matrix-test.pdf")

    per_page: dict[int, list[dict]] = {}
    for s in sections:
        per_page.setdefault(s.metadata.get("page", -1), []).append(
            {
                "kind": s.metadata.get("kind", "text"),
                "text": getattr(s.content, "text", "")[:60],
            }
        )

    for page in sorted(per_page):
        for item in per_page[page]:
            print(f"  p{page} [{item['kind']}] {item['text']}")

    def has_caption(page):
        return any(i["kind"] == "page_note" for i in per_page.get(page, []))

    def has_text(page):
        return any(i["kind"] == "text" for i in per_page.get(page, []))

    for page in (1, 2, 4):
        if not has_caption(page):
            failures.append(f"page {page}: caption expected but missing")
        if not has_text(page):
            failures.append(f"page {page}: text section missing")
    for page in (3, 5):
        if has_caption(page):
            failures.append(f"page {page}: caption should have been filtered")
        if not has_text(page):
            failures.append(f"page {page}: text section missing")

    # bogus xref must raise (caller catches and skips), not hang/crash hard
    parser_obj = PdfWithFiguresParser()
    with pymupdf.open(stream=build_pdf(), filetype="pdf") as doc:
        try:
            parser_obj._normalise_png(doc, 999999, 0, MAX_SIDE, 100)
            failures.append("bogus xref did not raise")
        except Exception:
            print("  bogus xref raised as expected (skip path)")

    if failures:
        print("\nMATRIX FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPDF FIGURES MATRIX PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
