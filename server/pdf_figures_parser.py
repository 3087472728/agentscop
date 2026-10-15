# -*- coding: utf-8 -*-
"""PDF parser with figure extraction and VLM captioning.

Extends the framework's page-text extraction with embedded-figure
handling for scanned/engineering documents whose illustrations carry
semantic content (figure titles, dimension labels, callouts) that the
text layer does not contain:

- MuPDF (``pymupdf``) extracts page text AND embedded bitmaps; it
  decodes every PDF image codec in-library (DCT / Flate / JBIG2 /
  CCITT / JPX) and normalises colour spaces, so image *format*
  differences never leak into the caller (a scanned GB-standard's
  JBIG2 bilevel pages and an ordinary JPEG photo take the same path).
- Every figure is normalised to RGB PNG (never raw PDF stream bytes),
  size-filtered and downscaled, then described by the vision-capable
  chat model (glm-5.3-flash via the Zhipu Coding Plan endpoint — the
  same ``ZAI_*`` credentials the chat models already use).
- Full-page scans (one image covering ~the whole page — typical for
  扫描版规范) use a page-level prompt covering figures, tables AND
  formulas; pages where the model answers "无" produce no caption, so
  pure-text scans cost one cheap vision call each but add no noise.
- Page notes are injected as extra text Sections (``[页注·页N]`` prefix,
  kind ``page_note``; one merged section per page) so the downstream
  chunker / text-only embedding / vector store stay untouched.

Failure semantics: undecodable figures and failed caption calls skip
that figure with a log line — indexing of the document never blocks.

Environment (project ``.env``, see ``auth.load_env_file``):
- ``ZAI_API_KEY`` / ``ZAI_BASE_URL`` — vision endpoint (required for
  captions; without them figures are skipped and only text is parsed);
- ``ZAI_MODEL`` — caption model (default ``glm-5.3-flash``);
- ``FIG_MAX_SIDE`` — downscale threshold, longest side in px
  (default 1600); ``FIG_MIN_DIM`` / ``FIG_MIN_BYTES`` — decorative
  image filters (defaults 100 px / 2048 bytes);
- ``FIG_THREADS`` — parallel caption calls (default 3).
"""
import asyncio
import base64
import concurrent.futures
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import pymupdf

from .auth import load_env_file
from agentscope.message import TextBlock
from agentscope.rag import ParserBase, Section

logger = logging.getLogger(__name__)

#: Prompt for a cropped/standalone figure.
FIGURE_PROMPT = (
    "这是中文技术规范文档《{filename}》第 {page} 页中的插图。"
    "请用一段简洁的中文描述这张图：图的内容/类型，以及图中可见的文字标注、"
    "编号和数值。图中若包含表格或数学公式，一并转写"
    "（表格用 Markdown，公式用 LaTeX，公式自带编号的保留原编号）。"
    "符号说明等普通叙述中的变量与符号用行内数学 $…$ 包裹（如 $K_m$、$Q_h$）。"
    "只描述可见内容，不要推测或补充图中没有的信息。"
)

#: Prompt for a full-page scan — the transcription REPLACES the page's
#: OCR text layer (which garbles formulas), so completeness matters.
PAGE_SCAN_PROMPT = (
    "这是中文技术规范文档《{filename}》第 {page} 页的整页扫描图。"
    "请完整转写本页全部内容，并按以下规则组织：\n"
    "1) 条文/正文文字：保持原文完整转写——勿概括、勿遗漏、勿改写；\n"
    "2) 插图：若有，先一句话概括主题，再描述其内容与图中可见的标注、数值；\n"
    "3) 表格：若有，先一句话概括表格主题，再将表格用 Markdown 完整转写"
    "——保留表头与全部行列；\n"
    "4) 公式：若包含数学公式，逐条用 LaTeX 精确转写；公式自带编号的保留"
    "原编号，无编号的不造编号；文中给出符号说明的，随后列出各符号含义；\n"
    "5) 插图标记：若本页包含插图/示意图/曲线图（数据表格不算插图），"
    "在回答的最开头单独一行输出【含插图】；纯文字页与纯表格页不要输出"
    "此标记；\n"
    "输出约定：公式用 LaTeX 写在独立公式块中——开头的 $$ 与结尾的 $$ "
    "各占一行（$$ 单独一行、公式内容单独一行、$$ 单独一行，公式块前后"
    "空行）；符号说明等普通叙述中的变量与符号用行内数学 $…$ 包裹"
    "（如 $K_m$、$Q_h$、$n$）；\n"
    "若本页完全为空白页，只输出一个字：无"
)

NO_FIGURE_ANSWER = "无"


def _is_no_content(text: str) -> bool:
    """Tolerant check for the model's empty-page answer."""
    normalized = text.strip().rstrip("。．.，,！!？?；; ")
    return normalized == NO_FIGURE_ANSWER or (
        normalized.startswith(NO_FIGURE_ANSWER) and len(normalized) <= 6
    )


def _clean_math_delimiters(text: str) -> str:
    """Normalise stray doubled dollar signs; keep inline $…$ and $$ blocks.

    Inline ``$…$`` wrapping of symbols is INTENTIONAL (the frontend
    renders it as math since the Streamdown singleDollarTextMath fix) —
    do not unwrap it. Only collapse accidental doubles like ``$$$$``.
    """
    return text.replace("$$$$", "$$")


def _normalise_math_blocks(text: str) -> str:
    """Convert single-line ``$$…$$`` into flow format ($$ on own lines).

    remark-math classifies a single-line ``$$…$$`` as *inline* math,
    which rehype-katex renders with displayMode=false — and KaTeX then
    rejects ``\\tag`` ("\\tag works only in display equations"), falling
    back to raw source text. Flow format (delimiters on their own lines)
    renders as display math.
    """
    if "$$" not in text:
        return text

    def repl(match: "re.Match[str]") -> str:
        raw = match.group(1)
        if "\n" in raw:
            # already flow format (delimiters on their own lines) — verbatim
            return match.group(0)
        inner = raw.strip()
        if not inner:
            return match.group(0)
        return f"\n$$\n{inner}\n$$\n"

    return re.sub(r"\$\$([^$]+?)\$\$", repl, text)


class PdfWithFiguresParser(ParserBase):
    """PDF parser: page text + VLM figure captions.

    Drop-in replacement for :class:`agentscope.rag.PDFParser` in
    ``create_app(knowledge_parsers=[...])`` — same media types and
    per-page Sections, plus one caption Section per usable embedded
    figure (see module docstring).
    """

    supported_media_types: list[str] = ["application/pdf"]

    @classmethod
    def supported_extensions(cls) -> list[str]:
        return [".pdf"]

    # -- public API ----------------------------------------------------

    async def parse(
        self,
        file: bytes | str,
        filename: str,
    ) -> list[Section]:
        """Page text sections + page-note sections (figure/table/formula).

        On pages where the vision transcription contains formulas and
        completed without truncation, the note REPLACES the page's OCR
        text section (which garbles formulas); otherwise the text layer
        is kept as the fallback.
        """
        return await asyncio.to_thread(self._parse_sync, file, filename)

    # -- pipeline (sync; runs in a worker thread) ----------------------

    def _parse_sync(
        self,
        file: bytes | str,
        filename: str,
    ) -> list[Section]:
        data = self._read(file)
        env_ready = self._load_credentials()
        text_sections: list[tuple[int, Section]] = []
        jobs: list[tuple[int, int, int, bytes, bool]] = []
        # (page_no, fig_idx, xref, png, is_fullpage); xref dedupes repeats
        seen_xref: set[int] = set()
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for page_no, page in enumerate(doc, start=1):
                text = page.get_text().strip()
                if text:
                    text_sections.append(
                        (
                            page_no,
                            Section(
                                content=TextBlock(text=text),
                                source=filename,
                                metadata={"page": page_no},
                            ),
                        )
                    )
                if not env_ready:
                    continue
                fig_idx = 0
                page_area = abs(page.rect)
                for info in page.get_images(full=True):
                    xref, smask = info[0], info[1]
                    if xref in seen_xref:
                        continue
                    seen_xref.add(xref)
                    try:
                        png = self._normalise_png(
                            doc,
                            xref,
                            smask,
                            int(os.environ.get("FIG_MAX_SIDE", "1600")),
                            int(os.environ.get("FIG_MIN_DIM", "100")),
                        )
                    except Exception as exc:  # noqa: BLE001 — skip bad images
                        logger.warning(
                            "figure xref %s on page %s skipped: %s",
                            xref,
                            page_no,
                            exc,
                        )
                        continue
                    if png is None:
                        continue
                    if len(png) < int(
                        os.environ.get("FIG_MIN_BYTES", "2048")
                    ):
                        continue
                    fig_idx += 1
                    is_fullpage = self._is_fullpage(page, page_area, xref)
                    jobs.append((page_no, fig_idx, xref, png, is_fullpage))

        if not jobs:
            return [s for _, s in sorted(text_sections)]

        # Caption phase — small thread pool; results assembled in page order.
        captions: dict[int, tuple[str, str] | None] = {}
        threads = max(1, int(os.environ.get("FIG_THREADS", "3")))
        with concurrent.futures.ThreadPoolExecutor(threads) as pool:
            futures = {
                pool.submit(
                    self._caption, png, filename, page_no, is_fullpage
                ): (page_no, xref)
                for page_no, fig_idx, xref, png, is_fullpage in jobs
            }
            for fut in concurrent.futures.as_completed(futures):
                page_no, xref = futures[fut]
                try:
                    captions[xref] = fut.result()
                except Exception as exc:  # noqa: BLE001 — defensive
                    logger.warning(
                        "caption failed for page %s: %s", page_no, exc
                    )
                    captions[xref] = None

        captions_by_page: dict[int, list[str]] = {}
        # Pages whose note contains a COMPLETE formula transcription
        # ($$ present, finish_reason != "length") replace the page's OCR
        # text section entirely — the text layer garbles formulas, while
        # the note is a faithful full-page transcription. Incomplete or
        # failed notes fall back to the text layer.
        pages_replaced: set[int] = set()
        for page_no, fig_idx, xref, _png, _fp in jobs:
            info = captions.get(xref)
            if not info:
                continue
            note, finish = info
            if _is_no_content(note):
                continue
            captions_by_page.setdefault(page_no, []).append(note.strip())
            if "$$" in note and finish != "length":
                pages_replaced.add(page_no)
        text_sections = [
            (page_no, section)
            for page_no, section in text_sections
            if page_no not in pages_replaced
        ]

        # Figure persistence: for pages the model marked 【含插图】, save
        # the page/figure image and append a markdown image link to the
        # note, so answers and detail views can show the original figure.
        figures_root = Path(
            os.environ.get(
                "FIGURES_DIR",
                str(
                    Path(__file__).resolve().parent.parent
                    / "blobs"
                    / "figures"
                ),
            )
        )
        public_base = os.environ.get(
            "FIG_PUBLIC_BASE", "http://localhost:8000"
        ).rstrip("/")
        doc_uuid = uuid4().hex[:12]

        caption_sections: list[tuple[int, Section]] = []
        for page_no, parts in sorted(captions_by_page.items()):
            note = "\n\n".join(parts)
            has_illustration = any("【含插图】" in part for part in parts)
            figure_paths: list[str] = []
            if has_illustration:
                links = []
                for job_page, fig_idx, _xref, png, _fp in jobs:
                    if job_page != page_no:
                        continue
                    name = f"p{page_no}-{fig_idx}.png"
                    out_path = figures_root / doc_uuid / name
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(png)
                    figure_paths.append(str(out_path))
                    url = f"{public_base}/figures/{doc_uuid}/{name}"
                    links.append(f"![插图·原文第{page_no}页]({url})")
                if links:
                    note = note + "\n\n" + "\n\n".join(links)
            section = Section(
                content=TextBlock(text=f"[页注·页{page_no}] {note}"),
                source=filename,
                metadata={
                    "page": page_no,
                    "kind": "page_note",
                },
            )
            if figure_paths:
                section.metadata["figure_paths"] = figure_paths
            caption_sections.append((page_no, section))
        all_sections = sorted(
            text_sections + caption_sections, key=lambda item: item[0]
        )
        return [s for _, s in all_sections]

    @staticmethod
    def _is_fullpage(
        page: "pymupdf.Page",
        page_area: float,
        xref: int,
    ) -> bool:
        """True when the image covers ~the whole page (scanned page)."""
        try:
            for rect in page.get_image_rects(xref):
                if abs(rect) >= 0.8 * page_area:
                    return True
        except Exception:  # noqa: BLE001 — detection is best-effort
            return False
        return False

    @staticmethod
    def _read(file: bytes | str) -> bytes:
        if isinstance(file, str):
            with open(file, "rb") as fp:
                return fp.read()
        return file

    # -- figure extraction with normalisation --------------------------

    def _normalise_png(
        self,
        doc: "pymupdf.Document",
        xref: int,
        smask: int,
        max_side: int,
        min_dim: int,
    ) -> bytes | None:
        """Decode any PDF image codec into RGB PNG; None if decorative.

        Raw stream bytes are never passed through: MuPDF decodes in
        library (JBIG2 / CCITT / JPX / DCT / Flate, CMYK / ICC / Indexed
        colour spaces) and the Pixmap is normalised to RGB PNG, so the
        VLM only ever sees a plain PNG regardless of the source format.
        """
        pix = pymupdf.Pixmap(doc, xref)
        if smask:  # flatten transparency onto the image
            pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
        if pix.colorspace is None or pix.colorspace.n > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if pix.width < min_dim or pix.height < min_dim:
            return None  # decorative rule/line/seal — not a figure
        png = pix.tobytes("png")
        pix = None
        return self._downscale(png, max_side)

    @staticmethod
    def _downscale(png: bytes, max_side: int) -> bytes:
        """Cap the longest side (VLM input limits + token budget)."""
        from PIL import Image

        with Image.open(io.BytesIO(png)) as im:
            if max(im.size) <= max_side:
                return png
            scale = max_side / max(im.size)
            resized = im.resize(
                (max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                Image.LANCZOS,
            )
            buf = io.BytesIO()
            resized.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()

    # -- vision captioning ---------------------------------------------

    def _load_credentials(self) -> bool:
        """Load .env and check the vision endpoint is configured."""
        load_env_file()
        if not os.environ.get("ZAI_API_KEY") or not os.environ.get(
            "ZAI_BASE_URL"
        ):
            logger.warning(
                "ZAI_API_KEY / ZAI_BASE_URL not configured — "
                "PDF figures are skipped, text-only parse."
            )
            return False
        return True

    def _caption(
        self,
        png: bytes,
        filename: str,
        page_no: int,
        is_fullpage: bool = False,
    ) -> tuple[str, str] | None:
        """Transcribe one figure/page with the vision model.

        Returns ``(note, finish_reason)`` or None on failure. No
        ``max_tokens`` cap is set — the model's own output limit applies
        and long transcriptions are handled downstream by the chunker /
        embed-time clipping. ``thinking`` is explicitly disabled:
        glm-5.3-flash is a thinking model and its hidden reasoning can
        otherwise leave ``content`` empty (observed as a silent None on
        table-heavy pages).
        """
        base = os.environ.get("ZAI_BASE_URL", "").rstrip("/")
        api_key = os.environ.get("ZAI_API_KEY")
        if not base or not api_key:
            return None
        model = os.environ.get("ZAI_MODEL", "glm-5.3-flash")
        template = PAGE_SCAN_PROMPT if is_fullpage else FIGURE_PROMPT
        body = {
            "model": model,
            "thinking": {"type": "disabled"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(png).decode()
                            },
                        },
                        {
                            "type": "text",
                            "text": template.format(
                                filename=filename, page=page_no
                            ),
                        },
                    ],
                }
            ],
            "temperature": 0.2,
        }
        last_error: Exception | str = ""
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    f"{base}/chat/completions",
                    data=json.dumps(body).encode(),
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + api_key,
                    },
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    out = json.loads(resp.read())
                choice = out["choices"][0]
                content = (choice.get("message") or {}).get("content")
                finish = choice.get("finish_reason") or ""
                if content and content.strip():
                    cleaned = _clean_math_delimiters(content.strip())
                    return _normalise_math_blocks(cleaned), finish
                raise ValueError(
                    f"empty content from vision model (finish={finish})"
                )
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2**attempt * 2)
                    continue
                break
            except (
                urllib.error.URLError,
                OSError,
                KeyError,
                ValueError,
            ) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
                continue
        logger.warning("caption failed for page %s: %s", page_no, last_error)
        return None
