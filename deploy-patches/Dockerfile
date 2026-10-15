# Thin custom image: official platform image + server patches.
# Rebuild after pulling a newer official image: update FROM tag, keep COPY lines.
# docker build -t agentscop-platform:custom-$(date +%Y%m%d) deploy-patches/
FROM crpi-d92le52k5f114023.cn-hangzhou.personal.cr.aliyuncs.com/agentrunner/agentscop-platform:20261014-134119

COPY container/_redis_storage.py /usr/local/lib/python3.13/site-packages/agentscope/app/storage/_redis_storage.py

COPY container/bge-small-zh-v1.5.yaml /usr/local/lib/python3.13/site-packages/agentscope/embedding/_openai/_models/bge-small-zh-v1.5.yaml
