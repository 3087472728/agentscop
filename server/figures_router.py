# -*- coding: utf-8 -*-
"""HTTP route for extracted document figures.

Serves the PNG snapshots written by ``PdfWithFiguresParser`` so that
markdown image links inside page notes (and answers) render in the UI:

    GET /figures/{doc_uuid}/{filename}    e.g. /figures/ab12cd/p143-1.png

Deliberately **unauthenticated**: ``<img>`` tags cannot carry Basic-auth
headers, so an authenticated route would break inline rendering. Figures
are page snapshots of user-uploaded documents in a local deployment —
the same trust boundary as the local workspace (no file fence). If this
ever gets exposed beyond localhost, front it with the download-token
mechanism instead.
"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

FIGURES_DIR = Path(
    os.environ.get(
        "FIGURES_DIR",
        str(Path(__file__).resolve().parent.parent / "blobs" / "figures"),
    )
)

figures_router = APIRouter(prefix="/figures", tags=["figures"])


@figures_router.get("/{doc_uuid}/{filename}")
async def get_figure(doc_uuid: str, filename: str) -> FileResponse:
    """Serve one extracted figure image (PNG)."""
    # path-traversal guard: only <12-hex>/<pN-M.png> shaped segments
    if (
        "/" in doc_uuid
        or "\\" in doc_uuid
        or "/" in filename
        or "\\" in filename
        or ".." in doc_uuid
        or ".." in filename
    ):
        raise HTTPException(status_code=404)
    path = (FIGURES_DIR / doc_uuid / filename).resolve()
    try:
        path.relative_to(FIGURES_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404)  # noqa: B904 — plain 404
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/png")
