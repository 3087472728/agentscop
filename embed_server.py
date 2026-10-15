# -*- coding: utf-8 -*-
# ============================================================
# 脚本名: embed_server.py
# 用途:   本地 Embedding 服务，提供 OpenAI 兼容 /v1/embeddings
#         接口，供 AgentScope 知识库文档向量化使用
# 运行:   systemd 服务 agentscope-embed.service（用户 agentrunner）
#         手动: /usr/bin/python3 /home/agentrunner/agentscop/embed_server.py
# 端口:   8003（仅绑定 127.0.0.1）
# 模型:   /home/agentrunner/models/BAAI--bge-small-zh-v1.5
#         （bge-small-zh-v1.5，512 维，GPU 推理，无 GPU 时自动回退 CPU）
# 说明:   忽略请求中的模型名，恒返回 bge 向量；按请求 dimensions
#         参数零填充/截断；提供 /v1/models 与 /healthz 端点
# ============================================================
# 解释器门槛: 本服务依赖 venv 中的新版 torch/sentence-transformers,
# 系统 Python 3.8 无法运行; 若旧 systemd 单元(ExecStart=/usr/bin/python3)
# 开机误启动, 在此直接失败退出, 避免其以 CPU 模式抢占 8003 端口。
import sys
if sys.version_info < (3, 10):
    sys.exit("embed_server requires Python >= 3.10 (use apps/.venv)")

import time

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Union, List
import uvicorn
import torch
from sentence_transformers import SentenceTransformer

# ---- 本地 BGE 模型路径（bge-small-zh-v1.5，输出 512 维向量）----
MODEL_PATH = "/home/agentrunner/models/BAAI--bge-small-zh-v1.5/snapshots/master"
# ---- 服务监听端口（仅本机访问，由 AgentScope API 服务内部调用）----
PORT = 8003

app = FastAPI(title="Local BGE Embedding Server")


class EmbedRequest(BaseModel):
    """OpenAI /v1/embeddings 请求体（OpenAI 兼容格式）。

    Attributes:
        model:      请求方指定的模型名，本服务忽略该字段，恒用本地 BGE。
        input:      待向量化的文本，支持单条字符串或字符串列表。
        dimensions: 目标向量维度，默认 512（与 BGE 原生维度一致）。
    """

    model: str = "bge-small-zh-v1.5"
    input: Union[str, List[str]]
    dimensions: int = 512


# ---- 服务启动时加载一次模型（优先 GPU，CUDA 不可用时回退 CPU）----
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
model = SentenceTransformer(MODEL_PATH, device=DEVICE)
print(f"bge-small-zh-v1.5 loaded on {DEVICE}", flush=True)


@app.post("/v1/embeddings")
async def embeddings(req: EmbedRequest):
    """核心端点：文本 → 向量（OpenAI 兼容响应格式）。

    流程: 统一转列表 → BGE 编码并归一化 → 按 dimensions 调整维度
    → 组装 OpenAI 格式响应。
    """
    texts = req.input if isinstance(req.input, list) else [req.input]
    vecs = model.encode(texts, normalize_embeddings=True).tolist()
    # 按请求的 dimensions 调整输出维度: d>512 零填充, d<512 截断。
    # 零填充对文档与查询一致处理, 余弦相似度保持不变, 因此知识库
    # 任选维度（如 512/1024）都能正常工作。
    d = req.dimensions or 512
    fixed = []
    for v in vecs:
        if d > len(v):
            v = v + [0.0] * (d - len(v))
        elif d < len(v):
            v = v[:d]
        fixed.append(v)
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": v}
            for i, v in enumerate(fixed)
        ],
        "model": req.model,
        "usage": {
            "prompt_tokens": sum(len(t) for t in texts),
            "total_tokens": sum(len(t) for t in texts),
        },
    }


@app.get("/v1/models")
async def models():
    """模型列表端点（OpenAI 兼容 + dimensions 扩展字段）。

    返回真实模型名与维度，供 AgentScope API 动态拉取凭据下可用模型，
    生成正确的模型卡片（name=bge-small-zh-v1.5, dimensions=512）。
    dimensions 为扩展字段（OpenAI 官方无此字段，但兼容端点可携带）。
    """
    return {
        "object": "list",
        "data": [
            {
                "id": "bge-small-zh-v1.5",
                "object": "model",
                "dimensions": 512,
            }
        ],
    }


@app.get("/healthz")
async def healthz():
    """健康检查端点——返回 ok 标志与当前时间戳，供 systemd/探活脚本使用。"""
    return {"ok": True, "ts": time.time()}


if __name__ == "__main__":
    # 仅绑定 127.0.0.1：不对外暴露，仅本机（AgentScope API 服务）调用
    uvicorn.run(app, host="127.0.0.1", port=PORT)
