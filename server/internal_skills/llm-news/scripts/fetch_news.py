#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取最新大模型动态（全部免费数据源，无需 API key）。

用法:
    python3 fetch_news.py [--days 2] [--focus 关键词] [--out 报告.md]

数据源:
    1. HuggingFace 最新 text-generation 模型
    2. HuggingFace 每日热门论文
    3. GitHub 近 N 天新创建的 LLM 仓库
    4. Hacker News 近 N 天 LLM 热帖
    5. arXiv cs.CL 最新提交
    6. Reddit r/LocalLLaMA 热帖（可能限流，失败自动跳过）
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) llm-news-skill/1.0"}
ATOM = "{http://www.w3.org/2005/Atom}"


def http_get(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def clip(text: str, n: int = 140) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:n] + ("…" if len(text) > n else "")


def fmt(items: list) -> str:
    if not items:
        return "-（本期无）\n"
    return "".join(
        f"- [{it['title']}]({it['url']}) — {it.get('summary', '')}"
        f"{('（' + it['meta'] + '）') if it.get('meta') else ''}\n"
        for it in items
    )


HF_HOSTS = ["https://huggingface.co", "https://hf-mirror.com"]


def http_get_hf(path: str) -> bytes:
    """HF 官方域名不可达时自动回退到国内镜像。"""
    last = None
    for host in HF_HOSTS:
        try:
            return http_get(host + path)
        except Exception as exc:
            last = exc
    raise last


def fetch_hf_models(days: int) -> list:
    """HuggingFace 最新 text-generation 模型。"""
    url = (
        "https://huggingface.co/api/models?sort=createdAt&direction=-1"
        "&limit=40&filter=text-generation"
    ).replace("https://huggingface.co", "")
    data = json.loads(http_get_hf(url))
    cutoff = time.time() - days * 86400
    out = []
    for m in data:
        try:
            ts = datetime.fromisoformat(
                m["createdAt"].replace("Z", "+00:00")
            ).timestamp()
        except (KeyError, ValueError):
            continue
        if ts < cutoff:
            continue
        out.append({
            "title": m["id"],
            "url": "https://huggingface.co/" + m["id"],
            "summary": "",
            "meta": f"↓{m.get('downloads', 0):,} ★{m.get('likes', 0)}"
                    f"{' 更新' + m['createdAt'][:10] if m.get('createdAt') else ''}",
        })
        if len(out) >= 15:
            break
    return out


def fetch_hf_papers(days: int) -> list:
    """HuggingFace 每日热门论文。"""
    data = json.loads(http_get_hf("/api/daily_papers"))
    out = []
    for it in data[:10]:
        p = it.get("paper", {})
        pid = p.get("id", "")
        out.append({
            "title": p.get("title", "").strip(),
            "url": f"https://huggingface.co/papers/{pid}" if pid else "",
            "summary": clip(p.get("summary", ""), 120),
            "meta": f"▲{p.get('upvotes', 0)}",
        })
    return out


def fetch_github(days: int) -> list:
    """GitHub 近 N 天新创建的 LLM 仓库。"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    q = urllib.parse.quote(f"topic:llm created:>{since}")
    url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=12"
    data = json.loads(http_get(url))
    out = []
    for r in data.get("items", []):
        out.append({
            "title": r["full_name"],
            "url": r["html_url"],
            "summary": clip(r.get("description") or "", 120),
            "meta": f"★{r.get('stargazers_count', 0)} 创建于 {r.get('created_at', '')[:10]}",
        })
    return out


def fetch_github_updates(days: int) -> list:
    """GitHub 近 N 天有推送的 LLM 仓库（反映发版/迭代动态）。"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    q = urllib.parse.quote(f"topic:llm stars:>50 pushed:>{since}")
    url = f"https://api.github.com/search/repositories?q={q}&sort=updated&order=desc&per_page=10"
    data = json.loads(http_get(url))
    out = []
    for r in data.get("items", []):
        out.append({
            "title": r["full_name"],
            "url": r["html_url"],
            "summary": clip(r.get("description") or "", 120),
            "meta": f"★{r.get('stargazers_count', 0)} 最近推送 {r.get('pushed_at', '')[:10]}",
        })
    return out


def fetch_hn(days: int) -> list:
    """Hacker News 近 N 天 LLM 相关热帖。"""
    cutoff = int(time.time() - days * 86400)
    q = urllib.parse.quote("LLM")
    url = (
        f"https://hn.algolia.com/api/v1/search_by_date?query={q}&tags=story"
        f"&hitsPerPage=15&numericFilters=created_at_i>{cutoff},points>10"
    )
    data = json.loads(http_get(url))
    out = []
    for h in data.get("hits", []):
        link = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append({
            "title": h.get("title", ""),
            "url": link,
            "summary": "",
            "meta": f"{h.get('points', 0)}分/{h.get('num_comments', 0)}评论",
        })
    return out


def fetch_arxiv(days: int) -> list:
    """arXiv cs.CL 最新提交。"""
    url = (
        "https://export.arxiv.org/api/query?search_query=cat:cs.CL"
        "&sortBy=submittedDate&sortOrder=descending&max_results=15"
    )
    root = ET.fromstring(http_get(url))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for e in root.iter(ATOM + "entry"):
        pub = (e.findtext(ATOM + "published") or "")[:10]
        try:
            dt = datetime.fromisoformat(pub).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt < cutoff:
            continue
        link = ""
        for l in e.findall(ATOM + "link"):
            if l.get("type") == "text/html" or not link:
                link = l.get("href", "")
        out.append({
            "title": re.sub(r"\s+", " ", e.findtext(ATOM + "title") or "").strip(),
            "url": link,
            "summary": clip(e.findtext(ATOM + "summary") or "", 120),
            "meta": pub,
        })
    return out


def fetch_reddit(days: int) -> list:
    """Reddit r/LocalLLaMA 热帖（RSS，可能限流）。"""
    t = "day" if days <= 2 else "week"
    root = ET.fromstring(
        http_get(f"https://www.reddit.com/r/LocalLLaMA/top/.rss?t={t}")
    )
    out = []
    for e in root.iter(ATOM + "entry"):
        title = e.findtext(ATOM + "title") or ""
        link = ""
        for l in e.findall(ATOM + "link"):
            link = l.get("href", "")
        out.append({"title": title, "url": link, "summary": "", "meta": "r/LocalLLaMA"})
        if len(out) >= 12:
            break
    return out


SOURCES = [
    ("🆕 HuggingFace 新模型", fetch_hf_models),
    ("📄 热门论文（HF Daily Papers）", fetch_hf_papers),
    ("💻 GitHub 新项目", fetch_github),
    ("🔧 GitHub 项目更新（热门）", fetch_github_updates),
    ("🔥 Hacker News 热帖", fetch_hn),
    ("🏛️ arXiv cs.CL 最新", fetch_arxiv),
    ("💬 Reddit r/LocalLLaMA", fetch_reddit),
]


def main() -> None:
    ap = argparse.ArgumentParser(description="抓取最新大模型动态")
    ap.add_argument("--days", type=int, default=2, help="回看天数（默认 2）")
    ap.add_argument("--focus", default="", help="高亮与关键词相关的条目")
    ap.add_argument("--out", default="", help="同时保存 Markdown 到指定文件")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    sections, focus_hits, statuses = [], [], []
    for name, fn in SOURCES:
        try:
            items = fn(args.days)
            sections.append((name, items))
            statuses.append(f"{name}: {len(items)} 条")
        except Exception as exc:  # 单源失败不拖垮整体
            sections.append((name, []))
            statuses.append(f"{name}: 失败（{type(exc).__name__}）")

    kw = args.focus.strip().lower()
    if kw:
        for name, items in sections:
            for it in items:
                blob = (it["title"] + it.get("summary", "")).lower()
                if kw in blob:
                    it["title"] = "⭐ " + it["title"]
                    focus_hits.append((name, it))

    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    md = [f"# 大模型情报速览（近 {args.days} 天）", f"_抓取时间：{today}_", ""]
    if kw:
        md += [f"## ⭐ 与「{args.focus}」相关", ""]
        md += [f"- 【{n.split(' ', 1)[-1]}】[{it['title'].lstrip('⭐ ')}]({it['url']}) {it.get('summary', '')}" for n, it in focus_hits] if focus_hits else ["-（未匹配到相关条目，以下为全部动态）"]
        md.append("")
    for name, items in sections:
        md += [f"## {name}", "", fmt(items)]

    md += ["---", f"_数据源状态：{'；'.join(statuses)}_"]
    report = "\n".join(md)

    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[llm-news] 已保存到 {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
