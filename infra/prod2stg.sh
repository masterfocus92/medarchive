#!/usr/bin/env bash
# prod2stg (ADR-016): стенд пересобирается из ПОСЛЕДНЕГО БЭКАПА — прод
# в синке не участвует вообще (читается только облако). Заодно ежедневно
# проверяется восстановимость бэкапа и репетируются миграции.
# Ночной таймер medarchive-prod2stg.timer (04:00) + ручной запуск в любой
# момент: sudo /usr/local/bin/medarchive-prod2stg
# Требует root (drop/create БД, рестарт сервиса).
set -euo pipefail

REMOTE="medarchive-crypt"
STG_DIR=/opt/medarchive/stg
STG_FILES=/var/lib/medarchive/stg/files
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# rclone-конфиг живёт у пользователя medarchive (гайд создаёт его там):
# скрипт работает root'ом, поэтому каждый вызов rclone — от medarchive.
# 755 на TMP: скачанный дамп должен уметь прочитать ещё и postgres
# (pg_restore ниже), а пишет его medarchive.
chown medarchive:medarchive "$TMP" && chmod 755 "$TMP"
rc() { sudo -u medarchive rclone "$@"; }

# Последний дамп — по имени (db_YYYY-MM-DD.dump сортируется лексикографически).
LATEST_DUMP="$(rc lsf "$REMOTE:daily/" --files-only | grep '^db_' | sort | tail -1 || true)"
if [ -z "$LATEST_DUMP" ]; then
  echo "ПРОВАЛ: в хранилище нет ни одного дампа — стенд не тронут" >&2
  exit 1
fi
STAMP="${LATEST_DUMP#db_}"; STAMP="${STAMP%.dump}"
FILES_ARCHIVE="files_${STAMP}.tar.gz"

echo "[$(date -Is)] prod2stg: беру бэкап от ${STAMP}"
rc copy "$REMOTE:daily/$LATEST_DUMP" "$TMP/" --checksum
rc copy "$REMOTE:daily/$FILES_ARCHIVE" "$TMP/" --checksum
[ -s "$TMP/$LATEST_DUMP" ] || { echo "ПРОВАЛ: дамп не скачался — стенд не тронут" >&2; exit 1; }
[ -s "$TMP/$FILES_ARCHIVE" ] || { echo "ПРОВАЛ: архив файлов не скачался — стенд не тронут" >&2; exit 1; }

echo "[$(date -Is)] prod2stg: пересоздаю medcard_stg…"
systemctl stop medarchive-stg
sudo -u postgres psql -qc "DROP DATABASE IF EXISTS medcard_stg WITH (FORCE)"
sudo -u postgres psql -qc "CREATE DATABASE medcard_stg OWNER medcard_stg"
# Расширение vector (Э7) — суперпользователем: роль medcard_stg создать его
# не вправе, а alembic upgrade ниже рассчитывает на IF NOT EXISTS (no-op).
# Нужно, пока прод-дамп без Э7; после — дамп сам восстановит расширение,
# строка останется безвредной.
sudo -u postgres psql -d medcard_stg -qc "CREATE EXTENSION IF NOT EXISTS vector"
# --no-owner --role: объекты прод-роли переезжают под роль стенда.
sudo -u postgres pg_restore --no-owner --role=medcard_stg -d medcard_stg "$TMP/$LATEST_DUMP"

echo "[$(date -Is)] prod2stg: миграции кодом стенда (репетиция прод-миграций)…"
sudo -u medarchive -H bash -c "cd '$STG_DIR' && uv run alembic upgrade head"

echo "[$(date -Is)] prod2stg: файлы записей…"
UNPACK="$TMP/unpack"; mkdir -p "$UNPACK"
tar -xzf "$TMP/$FILES_ARCHIVE" -C "$UNPACK"
# --delete: файлы, добавленные на стенде, штатно исчезают (плашка предупреждает).
rsync -a --delete "$UNPACK/files/" "$STG_FILES/"
chown -R medarchive:medarchive "$STG_FILES"

systemctl start medarchive-stg
sleep 2
curl -fsS http://127.0.0.1:8001/health >/dev/null \
  || { echo "ПРОВАЛ: стенд не поднялся после синка — journalctl -u medarchive-stg" >&2; exit 1; }

echo "[$(date -Is)] prod2stg завершён: стенд = прод от ${STAMP}"
