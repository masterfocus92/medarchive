#!/usr/bin/env bash
# Ночной бэкап прода (ADR-016): pg_dump + архив файлов → Yandex Object
# Storage через rclone crypt-remote «medarchive-crypt» (данные в облаке
# только шифрованные). Ретенция: 14 дневных, 6 недельных.
# Запускается от пользователя medarchive (таймер medarchive-backup.timer);
# пароль БД — в ~medarchive/.pgpass (см. runbook).
set -euo pipefail

REMOTE="medarchive-crypt"
STAMP="$(date +%F)"
FILES_DIR=/var/lib/medarchive/prod/files
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[$(date -Is)] бэкап: дамп БД…"
pg_dump -Fc -h localhost -U medcard_prod -d medcard_prod -f "$TMP/db_${STAMP}.dump"

echo "[$(date -Is)] бэкап: архив файлов…"
tar -czf "$TMP/files_${STAMP}.tar.gz" -C "$(dirname "$FILES_DIR")" "$(basename "$FILES_DIR")"

echo "[$(date -Is)] бэкап: выгрузка в хранилище…"
rclone copy "$TMP" "$REMOTE:daily/" --checksum

# Ретенция. Недельная копия — по воскресеньям, из только что загруженного.
if [ "$(date +%u)" = "7" ]; then
  rclone copy "$REMOTE:daily/db_${STAMP}.dump" "$REMOTE:weekly/"
  rclone copy "$REMOTE:daily/files_${STAMP}.tar.gz" "$REMOTE:weekly/"
fi
rclone delete "$REMOTE:daily/" --min-age 14d
rclone delete "$REMOTE:weekly/" --min-age 42d

echo "[$(date -Is)] бэкап завершён: db_${STAMP}.dump + files_${STAMP}.tar.gz"
