# RELEASE.md — процесс релиза

> Контур: ADR-015 (ветки, среды) · ADR-016 (бэкапы, prod2stg) · настройка VPS — `infra/README.md`.
> Прод: `mdkarta.online` · Стенд: `stg.mdkarta.online` (плашка «Тестовый стенд», данные пересобираются из бэкапа прода каждую ночь в 04:00).

## Схема

```
feature/* ──(приёмка владельцем, локальные тесты)──▶ stg ──(смоук на стенде)──▶ main
                                                     │                           │
                                               Actions: тесты                Actions: тесты
                                               → деплой стенда               → Approve → деплой прода
```

- `main` = прод. `stg` = стенд. Разработка — только в `feature/*`.
- В `stg` попадают принятые владельцем и зелёные локально фичи; в `main` — **только merge из `stg`**: на прод едет ровно то, что тестировалось.
- Прод-данные релизы не трогают: деплой = код + alembic-миграции, отрепетированные на стенде на копии боевых данных (стенд живёт на вчерашнем бэкапе прода).

## Штатный релиз

1. Убедиться, что фича принята и локально зелёная (`uv run pytest`, `uv run ruff check .`).
2. `git checkout stg && git merge --no-ff feature/<имя> && git push`
   → Actions: тесты → автодеплой стенда.
3. *(по желанию — свежие данные)* на VPS: `sudo /usr/local/bin/medarchive-prod2stg`.
4. Пройти **чек-лист смоука** (ниже) на `https://stg.mdkarta.online`.
5. `git checkout main && git merge --ff-only stg && git push`
   → Actions: тесты → job ждёт **Approve** (GitHub → Actions → deploy-prod → Review deployments).
6. После Approve — смоук прода: вход, лента, открыть одну запись.

## Чек-лист смоука стенда (~10 минут)

- [ ] Плашка «Тестовый стенд» видна на каждом экране.
- [ ] Вход; переключение профиля — акцент и заголовок ленты меняются.
- [ ] Лента: обе сортировки, бейджи только у неподтверждённых.
- [ ] Добавление с камеры: превью-лист, «＋ ещё страница», удаление кадра; пустая форма — «Сохранить» погашена с подсказкой.
- [ ] Сохранение → тост, запись в ленте; AI-разбор доехал (бейдж «разобрано — проверьте»).
- [ ] Экран проверки: AI-поля с синим пунктиром, принятие AI-пациента тапом по монограмме, «Сохранить» → подтверждена.
- [ ] Карточка: страницы скроллом, «Скачать оригинал», PDF-плашка (если есть PDF).
- [ ] Правка с карточки → возврат на карточку; «подтверждено» на месте.
- [ ] Удаление с подтверждением → исчезла из ленты, прямой URL — 404.
- [ ] `/health` обоих контуров — 200.

## Откат

```bash
# на VPS: вернуть контур на конкретный коммит (см. /var/log/medarchive/deploy.log)
sudo /usr/local/bin/medarchive-deploy prod <коммит>
# при миграциях в откатываемом релизе — сначала на текущей версии:
sudo -u medarchive -H bash -c 'cd /opt/medarchive/prod && uv run alembic downgrade <ревизия>'
```

Затем привести ветку в порядок (revert merge в `main`), иначе следующий деплой вернёт откатанное.

## Ручной prod2stg

`sudo /usr/local/bin/medarchive-prod2stg` — в любой момент; стенд пересобирается из **последнего бэкапа** (прод не участвует). Всё внесённое на стенде погибнет — это норма.

## Катастрофа: восстановление прода из бэкапа

Тот же механизм, что ежедневно отрабатывает на стенде:

```bash
systemctl stop medarchive-prod
rclone copy medarchive-crypt:daily/db_<дата>.dump /tmp/ && rclone copy medarchive-crypt:daily/files_<дата>.tar.gz /tmp/
sudo -u postgres psql -c "DROP DATABASE medcard_prod WITH (FORCE)" -c "CREATE DATABASE medcard_prod OWNER medcard_prod"
sudo -u postgres pg_restore --no-owner --role=medcard_prod -d medcard_prod /tmp/db_<дата>.dump
tar -xzf /tmp/files_<дата>.tar.gz -C /tmp && rsync -a --delete /tmp/files/ /var/lib/medarchive/prod/files/ && chown -R medarchive:medarchive /var/lib/medarchive/prod/files
sudo -u medarchive -H bash -c 'cd /opt/medarchive/prod && uv run alembic upgrade head'
systemctl start medarchive-prod
```

Ключ rclone-crypt — критический секрет: без него бэкапы нечитаемы. Оффлайн-копия — у владельца.

## Обновление системных скриптов

`deploy.sh`/`backup.sh`/`prod2stg.sh` живут в `/usr/local/bin/medarchive-*` и деплоем **не обновляются** (деплой меняет только код контуров). После изменения скриптов в `infra/` — повторить `cp` из `infra/README.md` §5–6.
