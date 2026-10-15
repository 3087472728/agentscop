FROM python:3.13-slim

WORKDIR /app

# 官方框架基座走 PyPI（agentscope==2.0.10，与 agentscope-official 本地
# 源码同版），业务依赖装进镜像层（缓存友好）；源码运行时 bind-mount
# （volumes: .:/app），改代码无需重建镜像。
# 本地开发机不用 PyPI 版：见 README「本地安装」一节（editable 指向
# agentscope-official 源码）。
COPY requirements.txt ./
# Tsinghua mirror: pypi.org's index is unreliable from CN networks
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
        "agentscope[service,storage-redis,storage-sql,channel,vdb-qdrant,rag]==2.0.10" \
        aiosqlite "pymupdf>=1.26" "pillow>=11.0" "websockets>=13" \
    && pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

EXPOSE 8000

# 统一入口：python main.py（uvicorn "server.app:app"）
CMD ["python", "main.py"]
