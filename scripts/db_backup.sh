#!/usr/bin/env bash
# 数据库备份：pg_dump 管道 gzip 写入 backups/，轮转保留最近 KEEP 份。
# 自动选择 compose 文件：存在 .env.prod 用生产栈，否则退回开发栈。
# 定时任务示例（宿主机 crontab）：
#   17 3 * * * cd /path/to/autohr && make backup-db >> backups/backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."

KEEP="${BACKUP_KEEP:-14}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="${BACKUP_DIR:-backups}"
OUT_FILE="${OUT_DIR}/autohr-${TIMESTAMP}.sql.gz"

if [ -f .env.prod ]; then
    COMPOSE=(docker compose -f docker-compose.prod.yml --env-file .env.prod)
else
    COMPOSE=(docker compose)
fi

mkdir -p "${OUT_DIR}"

echo "[backup] dumping via ${COMPOSE[*]} exec postgres ..."
"${COMPOSE[@]}" exec -T postgres sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "${OUT_FILE}"

SIZE=$(du -h "${OUT_FILE}" | cut -f1)
echo "[backup] done: ${OUT_FILE} (${SIZE})"

# 轮转：只保留最近 KEEP 份（ls 按名字排序 = 时间序）
LS_EXIT=0
ls -1t "${OUT_DIR}"/autohr-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "${old}" && echo "[backup] rotated: ${old}"
done || LS_EXIT=$?
exit 0
