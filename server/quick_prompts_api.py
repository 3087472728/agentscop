# -*- coding: utf-8 -*-
"""固定流程候选输入端点：GET /agent/{agent_id}/quick-prompts

从 agent-new 版 server/app_factory.py 迁出为独立模块。

配置文件：
- config/quick_prompts.json：{ "agent_id": [候选词, ...] }
  候选词支持 {占位符} 动态插值（数据源见 config/dynamic_sources.json）：
  内置 {date}=今天、{yesterday}=昨天；其余占位符按需执行只读 SQL
  （dynamic_sources.json 的 db_file 指向的数据库连接配置）取单值，
  插值失败显示 —；整表结果缓存 60 秒，避免每次打开页面都查库。

注意：dynamic_sources.json 引用的数据库连接文件（如
config/db_sixnet.json）含敏感信息、不随仓库分发；缺失时占位符
一律渲染为 "—"，端点本身照常工作。psycopg2 仅在配置了 SQL 占位符
且请求触发时才导入。
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI

from . import paths

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_QP_CACHE_TTL = 60.0
_qp_cache: dict = {"ts": 0.0, "data": {}}


def _bj_date(offset_days: int = 0) -> str:
    d = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=offset_days)
    return d.strftime("%Y-%m-%d")


def _load_json_file(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _query_configured_db(sql: str):
    """执行 dynamic_sources.json 配置的只读 SQL，返回单值。"""
    import psycopg2  # 延迟导入：未配置 SQL 占位符的环境无需安装

    src = _load_json_file(os.path.join(paths.CONFIG_DIR, "dynamic_sources.json"))
    db = _load_json_file(
        os.path.normpath(os.path.join(paths.CONFIG_DIR, src["db_file"]))
    )
    conn = psycopg2.connect(
        host=db["host"],
        port=db.get("port", 5432),
        dbname=db["database"],
        user=db["user"],
        password=db["password"],
        sslmode=db.get("sslmode", "disable"),
        options="-c search_path=sixnet,public -c statement_timeout=8000",
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _render_dynamic(text: str, sources: dict) -> str:
    builtins = {"date": _bj_date(0), "yesterday": _bj_date(-1)}

    def _repl(m: "re.Match") -> str:
        key = m.group(1)
        if key in builtins:
            return str(builtins[key])
        sql = sources.get(key)
        if not sql:
            return m.group(0)  # 未配置的占位符原样保留
        try:
            return str(_query_configured_db(sql))
        except Exception:
            return "—"

    return _PLACEHOLDER_RE.sub(_repl, text)


async def _render_prompt(item, sources: dict) -> str:
    text = item.get("text", "") if isinstance(item, dict) else str(item)
    if "{" in text:
        text = await asyncio.to_thread(_render_dynamic, text, sources)
    return text


def setup(app: FastAPI) -> None:
    @app.get("/agent/{agent_id}/quick-prompts")
    async def get_quick_prompts(agent_id: str):
        """返回该智能体在对话框上方的候选词（支持动态占位符插值）。"""
        now = time.time()
        if now - _qp_cache["ts"] > _QP_CACHE_TTL or not _qp_cache["data"]:
            try:
                mapping = _load_json_file(
                    os.path.join(paths.CONFIG_DIR, "quick_prompts.json")
                )
            except Exception:
                mapping = {}
            try:
                sources = _load_json_file(
                    os.path.join(paths.CONFIG_DIR, "dynamic_sources.json")
                ).get("placeholders", {})
            except Exception:
                sources = {}
            data = {}
            for aid, prompts in mapping.items():
                if aid.startswith("_") or not isinstance(prompts, list):
                    continue
                data[aid] = list(
                    await asyncio.gather(
                        *[_render_prompt(p, sources) for p in prompts]
                    )
                )
            _qp_cache["ts"] = now
            _qp_cache["data"] = data
        return {"prompts": _qp_cache["data"].get(agent_id, [])}
