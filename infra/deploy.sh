#!/usr/bin/env bash
# Деплой контура (ADR-015): deploy.sh <prod|stg> [ref]
#   prod → ветка main, порт 8000; stg → ветка stg, порт 8001.
#   ref — необязательный явный коммит/тег для отката.
# Запускается root'ом (или deploy-пользователем через sudo — см. runbook).
# Идемпотентен: повторный запуск с тем же ref безвреден.
set -euo pipefail

CONTOUR="${1:?использование: deploy.sh <prod|stg> [ref]}"
REF="${2:-}"

case "$CONTOUR" in
  prod) DIR=/opt/medarchive/prod BRANCH=main PORT=8000 ;;
  stg)  DIR=/opt/medarchive/stg  BRANCH=stg  PORT=8001 ;;
  *) echo "неизвестный контур: $CONTOUR (ожидается prod|stg)" >&2; exit 2 ;;
esac

LOG=/var/log/medarchive/deploy.log
mkdir -p "$(dirname "$LOG")"

# Код и зависимости — от имени владельца каталога, не root'а:
# root в каталоге приложения оставляет файлы, недоступные сервису.
as_app() { sudo -u medarchive -H bash -c "cd '$DIR' && $*"; }

as_app "git fetch origin --prune"
if [ -n "$REF" ]; then
  # Откат/точный релиз: detach на явный ref.
  as_app "git checkout --detach '$REF'"
else
  as_app "git checkout -q '$BRANCH' && git reset --hard 'origin/$BRANCH'"
fi

as_app "uv sync --frozen"
# Миграции — на БД контура (.env читается из каталога контура).
# Репетиция на стенде гарантирована порядком веток: stg деплоится раньше main.
as_app "uv run alembic upgrade head"

systemctl restart "medarchive-$CONTOUR"

# Smoke: сервис жив. Немного ждём старт uvicorn.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    DEPLOYED=$(as_app "git rev-parse --short HEAD")
    echo "$(date -Is) $CONTOUR ok $DEPLOYED" >> "$LOG"
    echo "деплой $CONTOUR завершён: $DEPLOYED"
    exit 0
  fi
done

echo "$(date -Is) $CONTOUR FAIL smoke" >> "$LOG"
echo "ПРОВАЛ: $CONTOUR не отвечает на /health после рестарта — смотри journalctl -u medarchive-$CONTOUR" >&2
exit 1
