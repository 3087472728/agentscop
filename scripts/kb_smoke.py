# -*- coding: utf-8 -*-
"""知识库全链路冒烟（对标 chat_smoke.py）。

前置：start-all 已拉起（Redis / Qdrant 6333 / TEI 19556 / FastAPI 8000），
且 configure_embedding.py 已为 admin 写入 local_bge 凭据。

链路：环境探针（Qdrant/TEI）→ embedding 目录检查 → 建临时知识库 →
上传中文 txt → 轮询异步索引至 ready → 语义检索断言命中 → 删除清理。

Usage:
    python kb_smoke.py            # 默认 admin
    python kb_smoke.py --user u2
"""
import argparse
import datetime
import json
import time
import urllib.error
import urllib.request
import uuid

from common import BASE_URL, api, auth_headers, http_ssl_context
from configure_embedding import DEFAULT_TEI_BASE_URL, ensure_bge_credential
from common import load_env

KB_NAME_PREFIX = "kb-smoke"
MODEL_NAME = "bge-small-zh-v1.5"
MODEL_DIMENSIONS = 512

DOC_TEXT = (
    "agentscope-extra 冒烟测试文档。\n"
    "机密备件 K-9527 存放于蓝色仓库三号柜，钥匙由值班班长保管。\n"
    "本段落用于验证中文语义检索是否命中目标分块。"
).encode("utf-8")

QUERY = "K-9527 备件放在哪里？"


def probe(name: str, url: str) -> None:
    """Fail fast when a dependency is not reachable."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            print(f"  probe {name}: HTTP {resp.status} OK ({url})")
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"probe {name} FAILED ({url}): {exc.reason}\n"
            "先跑 .\\start-all.ps1，确认容器 agentscope-qdrant / "
            "agentscope-tei 处于运行状态。"
        )


def upload_document(kb_id: str, filename: str, content: bytes) -> str:
    """Multipart upload via urllib (scripts stay stdlib-only)."""
    boundary = f"----kb_smoke_{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\n'.encode(),
            b"Content-Type: text/plain\r\n\r\n",
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    req = urllib.request.Request(
        f"{BASE_URL}/knowledge_bases/{kb_id}/documents",
        data=body,
        method="POST",
        headers={
            **auth_headers(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(
        req, timeout=60, context=http_ssl_context()
    ) as resp:
        return json.loads(resp.read())["document_id"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=None, help="Run as this user")
    args = parser.parse_args()
    if args.user:
        raise SystemExit("kb_smoke currently runs as admin only.")

    failures: list[str] = []
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    print("=== 1. probes ===")
    qdrant_url = load_env().get(
        "AGENTS_QDRANT_URL", "http://localhost:6333"
    ).rstrip("/")
    probe("Qdrant", f"{qdrant_url}/healthz")
    tei_base = load_env().get("TEI_BASE_URL", DEFAULT_TEI_BASE_URL)
    probe("TEI", tei_base.replace("/v1", "") + "/health")

    print("=== 2. ensure local_bge credential ===")
    ensure_bge_credential("admin", tei_base)
    _, listed = api("GET", "/credential/", user="admin")
    credential_id = next(
        v["id"]
        for v in listed["credentials"]
        if (v.get("data") or {}).get("type") == "local_bge"
    )

    print("=== 3. embedding catalog ===")
    _, catalog = api("GET", "/knowledge_bases/embedding_models", user="admin")
    providers = {
        (p["credential"]["data"] or {}).get("type"): p["models"]
        for p in catalog["providers"]
    }
    print(f"  provider types: {sorted(providers)}")
    bge_cards = providers.get("local_bge", [])
    card = next(
        (c for c in bge_cards if c["name"] == MODEL_NAME), None
    )
    if card is None:
        failures.append(
            f"local_bge catalog lacks {MODEL_NAME}: "
            f"{[c['name'] for c in bge_cards]}"
        )
    else:
        print(f"  bge card: dims={card['dimensions']}")
        if card["dimensions"] != MODEL_DIMENSIONS:
            failures.append(
                f"{MODEL_NAME} dimensions {card['dimensions']} != "
                f"{MODEL_DIMENSIONS}"
            )
    if "zhipu_coding_plan" in providers:
        failures.append(
            "zhipu_coding_plan still exposes embedding cards "
            "(expected empty catalog)"
        )

    print("=== 4. create temp knowledge base ===")
    kb_name = f"{KB_NAME_PREFIX}-{stamp}"
    _, created = api(
        "POST",
        "/knowledge_bases/",
        {
            "name": kb_name,
            "description": "temporary kb from kb_smoke.py",
            "embedding_model_config": {
                "type": "local_bge",
                "credential_id": credential_id,
                "model": MODEL_NAME,
                "dimensions": MODEL_DIMENSIONS,
                "parameters": {},
            },
            "chunker_config": {
                "type": "approx_token",
                "parameters": {"chunk_size": 300, "overlap": 50},
            },
        },
        user="admin",
    )
    kb_id = created["knowledge_base_id"]
    print(f"  kb: {kb_name} ({kb_id})")

    try:
        print("=== 5. upload + wait for indexing ===")
        doc_id = upload_document(kb_id, "kb_smoke.txt", DOC_TEXT)
        print(f"  document: {doc_id}")
        deadline = time.time() + 180
        status = None
        while time.time() < deadline:
            _, docs = api(
                "GET", f"/knowledge_bases/{kb_id}/documents", user="admin"
            )
            doc = next(
                (d for d in docs["documents"] if d["id"] == doc_id), None
            )
            status = (doc or {}).get("status")
            if status in ("ready", "error"):
                break
            time.sleep(3)
        print(f"  status: {status}")
        if status != "ready":
            failures.append(
                f"document ended as '{status}' "
                f"(error={(doc or {}).get('error')})"
            )

        print("=== 6. semantic search ===")
        _, hits = api(
            "POST",
            f"/knowledge_bases/{kb_id}/search",
            {"query": QUERY, "top_k": 3},
            user="admin",
        )
        total = hits.get("total", len(hits.get("results", [])))
        print(f"  hits: {total}")
        for r in hits.get("results", [])[:3]:
            print(f"  score={r.get('score'):.4f} chunk={str(r['chunk'])[:80]!r}")
        if total < 1:
            failures.append("search returned no hits")
        else:
            top = hits["results"][0]
            if top.get("document_id") != doc_id:
                failures.append(
                    f"top hit from {top.get('document_id')}, expected {doc_id}"
                )
    finally:
        print("=== 7. cleanup ===")
        try:
            api("DELETE", f"/knowledge_bases/{kb_id}", user="admin")
            print(f"  deleted kb {kb_id}")
        except RuntimeError as exc:
            failures.append(f"cleanup failed: {exc}")

    print()
    if failures:
        print("FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("KB SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
