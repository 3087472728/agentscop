#!/usr/bin/env bash
# ============================================================
# 脚本名: backup_apps.sh
# 用途:   AgentScope 数据每日定时备份
# 备份内容: blobs(1.8G) + qdrant_data(714M) + agentscope.db + 配置/工作区
# 策略: 每日 tar 打包, 保留最近 7 份; 数据库用 sqlite .backup 保证一致性
# 备份目录: /home/agentrunner/backups/
# ============================================================
set -uo pipefail

APPS_DIR="/home/agentrunner/agentscop"
BACKUP_DIR="/home/agentrunner/backups"
KEEP=7
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${BACKUP_DIR}/backup.log"

mkdir -p "${BACKUP_DIR}"
exec >> "${LOG_FILE}" 2>&1
echo "===== backup start ${STAMP} ====="

# 1. 数据库一致性快照 (sqlite online backup)
python3 - "${APPS_DIR}/agentscope.db" "${BACKUP_DIR}/agentscope-${STAMP}.db" <<'EOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
d.close(); s.close()
print("sqlite backup ok")
EOF

# 2. 向量库 + blob 文件 + 配置 (tar 打包)
#    qdrant_data 与 blobs 均为追加式写入, 凌晨低负载时打包基本安全
cd /home/agentrunner || exit 1
tar -czf "${BACKUP_DIR}/apps-${STAMP}.tar.gz" \
    agentscop/qdrant_data \
    agentscop/blobs \
    agentscop/workspaces \
    agentscop/main.py \
    agentscop/server \
    agentscop/config \
    agentscop/embed_server.py \
    agentscop/scripts/backup_apps.sh \
    agentscop/make-dl-link.sh \
    agentscop/requirements.txt \
    agentscop/agentscope.db \
    agentscop/agentscope.db-wal \
    agentscop/agentscope.db-shm \
    2>/dev/null

# 3. 轮转: 保留最近 KEEP 份, 删除更早的
ls -1t "${BACKUP_DIR}"/apps-*.tar.gz 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
ls -1t "${BACKUP_DIR}"/agentscope-*.db 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f

# 4. 报告
echo "backup done:"
du -sh "${BACKUP_DIR}"/apps-${STAMP}.tar.gz "${BACKUP_DIR}"/agentscope-${STAMP}.db 2>/dev/null || true
echo "===== backup end ${STAMP} ====="
