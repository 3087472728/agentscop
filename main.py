# -*- coding: utf-8 -*-
"""智能体平台启动入口（项目根目录运行）。

    python main.py

默认以零依赖模式启动：SQLite 存储 + 进程内本地向量库 + 文件角色表。
生产/容器部署通过 .env 切换（AGENTS_STORAGE=redis、
AGENTS_QDRANT_MODE=url 等），见 .env.example 与 deploy/DEPLOY-ALIYUN.md。
"""
import os
import sys

import uvicorn

from server.app import BACKEND_PORT, SSL_CERTFILE, SSL_KEYFILE

if __name__ == "__main__":
    # AGENTS_RELOAD 覆盖平台默认值（Windows 下默认关闭热重载：
    # 热重载强制使用 SelectorEventLoop，无法派生内置工具依赖的
    # 子进程）。容器设 AGENTS_RELOAD=0 —— 有重启策略就不需要重载。
    _reload = os.getenv("AGENTS_RELOAD", "").strip().lower()
    # TLS 进程内终结（AGENTS_SSL_CERTFILE / AGENTS_SSL_KEYFILE）
    _ssl = (
        {"ssl_certfile": SSL_CERTFILE, "ssl_keyfile": SSL_KEYFILE}
        if SSL_CERTFILE and SSL_KEYFILE
        else {}
    )
    uvicorn.run(
        "server.app:app",
        host="0.0.0.0",
        port=BACKEND_PORT,
        reload=(
            _reload in ("1", "true", "yes", "on")
            if _reload
            else sys.platform != "win32"
        ),
        **_ssl,
    )
