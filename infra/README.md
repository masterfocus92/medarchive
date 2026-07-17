# Гайд: развёртывание релизного контура (этап 6.5)

Полный список действий владельца — от DNS до первого автодеплоя. Основа: ADR-015 (контур), ADR-016 (бэкапы/prod2stg), ADR-017 (нативный PG). Процесс релиза после настройки — `RELEASE.md`.

Обозначения: **[Локально]** — ваш компьютер · **[Панель]** — веб-интерфейс (регистратор/Яндекс/GitHub) · **[VPS]** — по SSH под root. После каждого шага — ✅ проверка: не идите дальше, пока она не сходится.

Замените по тексту: `<IP_VPS>` — IP сервера; пароли `CHANGE_ME_*` — придумывайте (генератор: `openssl rand -hex 32`) и **записывайте в надёжное место** — они понадобятся в нескольких шагах.

Время: ~1,5 часа. Порядок важен: DNS — первым (записи расходятся не мгновенно).

---

## Шаг 1. DNS — два домена на VPS **[Панель регистратора]**

1. Откройте управление DNS-зоной `mdkarta.online`.
2. Создайте две A-записи:
   - имя `@` (или пусто) → `<IP_VPS>`
   - имя `stg` → `<IP_VPS>`

✅ **[Локально]** через 5–30 минут: `dig +short mdkarta.online` и `dig +short stg.mdkarta.online` возвращают `<IP_VPS>`.

## Шаг 2. Яндекс Облако — бакет для бэкапов **[Панель]**

> Названия пунктов меню консоли могут немного отличаться — суть: бакет + сервисный аккаунт + статический S3-ключ.

1. В консоли Яндекс Облака (console.yandex.cloud) создайте **Object Storage → бакет**: имя `mdkarta-backup`, доступ — приватный, класс хранения по умолчанию — «Холодное» (бэкапы пишутся редко, читаются ещё реже).
2. **IAM → Сервисные аккаунты → создать**: имя `mdkarta-backup-writer`, роль `storage.editor` (можно только на этот бакет).
3. У сервисного аккаунта создайте **статический ключ доступа** — получите пару «идентификатор ключа» (`ACCESS_KEY`) и «секретный ключ» (`SECRET_KEY_S3`). Секрет показывается один раз — сохраните.

✅ Ключи записаны, бакет виден в консоли пустым.

## Шаг 3. База VPS: swap, пользователи, uv **[VPS]**

```bash
ssh root@<IP_VPS>

# Swap 2 ГБ (ADR-017: 1 ГБ RAM — страховка от OOM)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Пользователи: medarchive — владелец приложения; deploy — только для CI
useradd --system --create-home --shell /bin/bash medarchive
useradd --create-home --shell /bin/bash deploy

# Каталоги контуров, файлов записей, логов
install -d -o medarchive -g medarchive /opt/medarchive /var/lib/medarchive/prod/files /var/lib/medarchive/stg/files /var/log/medarchive
chmod 750 /var/lib/medarchive/prod/files /var/lib/medarchive/stg/files

# uv — в /usr/local/bin (путь зашит в systemd-юниты)
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
```

✅ `free -h` показывает `Swap: 2.0Gi` · `uv --version` отвечает · `id medarchive` и `id deploy` существуют.

## Шаг 4. PostgreSQL 16 + pgvector **[VPS]**

```bash
apt update && apt install -y postgresql-common
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
apt install -y postgresql-16 postgresql-16-pgvector
```

Роли и БД — по одной команде (heredoc ломается при вставке в терминал; подставьте свои пароли вместо `CHANGE_ME_PROD` / `CHANGE_ME_STG` — они пойдут в `.env` контуров на шаге 5). Ошибка `already exists` на повторном прогоне безвредна:

```bash
sudo -u postgres psql -c "CREATE ROLE medcard_prod LOGIN PASSWORD 'CHANGE_ME_PROD';"
sudo -u postgres psql -c "CREATE ROLE medcard_stg  LOGIN PASSWORD 'CHANGE_ME_STG';"
sudo -u postgres psql -c "CREATE DATABASE medcard_prod OWNER medcard_prod;"
sudo -u postgres psql -c "CREATE DATABASE medcard_stg  OWNER medcard_stg;"

# Пароль прод-БД для ночного pg_dump (бэкап идёт от medarchive)
sudo -u medarchive bash -c 'echo "localhost:5432:medcard_prod:medcard_prod:CHANGE_ME_PROD" > ~/.pgpass && chmod 600 ~/.pgpass'
```

Тюнинг под 1 ГБ RAM — конфиг уже в репозитории, скопируем на шаге 5 после клона; пока продолжайте.

✅ `sudo -u postgres psql -c '\l'` показывает `medcard_prod` и `medcard_stg`.

## Шаг 5. Контуры приложения **[VPS]**

```bash
# Клоны (репозиторий публичный — токены не нужны)
sudo -u medarchive git clone -b main https://github.com/masterfocus92/medarchive.git /opt/medarchive/prod
sudo -u medarchive git clone -b stg  https://github.com/masterfocus92/medarchive.git /opt/medarchive/stg

# Тюнинг PG из репозитория (обещан на шаге 4)
cp /opt/medarchive/prod/infra/postgres/medarchive.conf /etc/postgresql/16/main/conf.d/
systemctl restart postgresql
```

`.env` контуров — по шаблонам. Заполните все `CHANGE_ME`: пароли БД из шага 4, **разные** `SECRET_KEY` (`openssl rand -hex 32` дважды), ключи RouterAI (лучше два отдельных — расходы стенда видны отдельно):

```bash
sudo -u medarchive cp /opt/medarchive/prod/infra/env.prod.example /opt/medarchive/prod/.env
sudo -u medarchive cp /opt/medarchive/stg/infra/env.stg.example  /opt/medarchive/stg/.env
sudo -u medarchive nano /opt/medarchive/prod/.env
sudo -u medarchive nano /opt/medarchive/stg/.env
```

Зависимости, миграции, seed семьи (`.env.seed` — реальные данные семьи, только на сервере, шаблон подскажет поля):

```bash
sudo -u medarchive -H bash -c 'cd /opt/medarchive/prod && uv sync --frozen && uv run alembic upgrade head'
sudo -u medarchive -H bash -c 'cd /opt/medarchive/stg  && uv sync --frozen && uv run alembic upgrade head'

sudo -u medarchive cp /opt/medarchive/prod/.env.seed.example /opt/medarchive/prod/.env.seed
sudo -u medarchive nano /opt/medarchive/prod/.env.seed
sudo -u medarchive -H bash -c 'cd /opt/medarchive/prod && uv run python -m app.seed'
```

systemd-юниты:

```bash
cp /opt/medarchive/prod/infra/systemd/medarchive-prod.service /etc/systemd/system/
cp /opt/medarchive/prod/infra/systemd/medarchive-stg.service  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now medarchive-prod medarchive-stg
```

✅ `curl -s localhost:8000/health` и `curl -s localhost:8001/health` — оба `{"status":"ok"}`. Seed ответил «Семья создана.». Если сервис не поднялся: `journalctl -u medarchive-prod -n 50`.

## Шаг 6. Caddy: домены поверх существующего **[VPS]**

Перед правкой — копия текущего конфига (там живёт ваша вторая PWA):

```bash
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak
cat /opt/medarchive/prod/infra/caddy/Caddyfile.medarchive >> /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy
```

Firewall (если ufw ещё не включён — сначала SSH, иначе отрежете себя):

```bash
ufw allow OpenSSH && ufw allow 80,443/tcp && ufw enable
```

✅ `https://mdkarta.online` — форма входа, замочек валидный · `https://stg.mdkarta.online` — то же **плюс красная плашка «Тестовый стенд»** · ваша старая PWA открывается как раньше · вход прод-учёткой из seed работает.

## Шаг 7. Бэкапы и prod2stg **[VPS]**

rclone: S3-remote на бакет + crypt-обёртка (в облако уходит только шифрованное). Пароль шифрования придумайте и **сохраните оффлайн** (второй `CHANGE_ME_SALT` — тоже): без них бэкапы нечитаемы, это самый критический секрет контура.

```bash
apt install -y rclone

sudo -u medarchive rclone config create yandex s3 \
  provider=Other endpoint=storage.yandexcloud.net region=ru-central1 \
  access_key_id=<ACCESS_KEY из шага 2> secret_access_key=<SECRET_KEY_S3 из шага 2>

PASS=$(rclone obscure 'CHANGE_ME_CRYPT_PASSWORD')
SALT=$(rclone obscure 'CHANGE_ME_SALT')
sudo -u medarchive rclone config create medarchive-crypt crypt \
  remote=yandex:mdkarta-backup password=$PASS password2=$SALT
```

Скрипты и таймеры (скрипты живут в `/usr/local/bin` и деплоем не обновляются — при их изменении в репо повторить эти `cp`):

```bash
cp /opt/medarchive/prod/infra/backup.sh   /usr/local/bin/medarchive-backup   && chmod 755 /usr/local/bin/medarchive-backup
cp /opt/medarchive/prod/infra/prod2stg.sh /usr/local/bin/medarchive-prod2stg && chmod 755 /usr/local/bin/medarchive-prod2stg
cp /opt/medarchive/prod/infra/systemd/medarchive-backup.service   /etc/systemd/system/
cp /opt/medarchive/prod/infra/systemd/medarchive-backup.timer     /etc/systemd/system/
cp /opt/medarchive/prod/infra/systemd/medarchive-prod2stg.service /etc/systemd/system/
cp /opt/medarchive/prod/infra/systemd/medarchive-prod2stg.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now medarchive-backup.timer medarchive-prod2stg.timer
```

Первый прогон руками — сразу проверяем всю цепочку:

```bash
sudo -u medarchive /usr/local/bin/medarchive-backup
rclone lsf medarchive-crypt:daily/    # видны db_*.dump и files_*.tar.gz
rclone lsf yandex:mdkarta-backup -R | head   # а тут — только шифрованная тарабарщина
/usr/local/bin/medarchive-prod2stg    # стенд пересобрался из бэкапа
```

✅ Оба листинга сходятся · prod2stg завершился «стенд = прод от <дата>» · на `stg.mdkarta.online` виден контент прода · `systemctl list-timers | grep medarchive` — два таймера с временем следующего запуска.

## Шаг 8. Деплой-доступ и GitHub **[VPS + Панель GitHub]**

**[VPS]** — установка deploy-скрипта и ограниченного CI-доступа:

```bash
cp /opt/medarchive/prod/infra/deploy.sh /usr/local/bin/medarchive-deploy && chmod 755 /usr/local/bin/medarchive-deploy

sudo -u deploy ssh-keygen -t ed25519 -N '' -f /home/deploy/.ssh/id_ci
cat /home/deploy/.ssh/id_ci.pub >> /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
echo 'deploy ALL=(root) NOPASSWD: /usr/local/bin/medarchive-deploy' > /etc/sudoers.d/medarchive-deploy
chmod 440 /etc/sudoers.d/medarchive-deploy

cat /home/deploy/.ssh/id_ci   # приватный ключ — скопируйте целиком для GitHub
```

**[Панель GitHub]** — Settings репозитория `masterfocus92/medarchive`:

1. **Secrets and variables → Actions → New repository secret**, два секрета:
   - `DEPLOY_HOST` = `<IP_VPS>`
   - `DEPLOY_SSH_KEY` = содержимое `/home/deploy/.ssh/id_ci` (весь блок с BEGIN/END).
2. **Environments → New environment** → имя `production` → **Required reviewers** → добавьте себя → Save. Это и есть кнопка Approve перед продом.
3. **Branches → Add branch protection rule** для `main`: Require a pull request before merging + Require status checks (`tests`). Merge в `main` — только из `stg`.
4. После добавления секрета удалите приватный ключ с сервера: **[VPS]** `rm /home/deploy/.ssh/id_ci`.

✅ В Actions перезапустите последний прогон `deploy-stg` (Re-run) — теперь он зелёный и стенд обновился сам.

## Шаг 9. Финальная проверка контура (= приёмка этапа)

- [ ] `https://mdkarta.online` — вход, лента; плашки нет.
- [ ] `https://stg.mdkarta.online` — то же + плашка «Тестовый стенд».
- [ ] Пуш любого коммита в `stg` → Actions зелёные → стенд обновился без ручных действий.
- [ ] Merge `stg`→`main` → job `deploy-prod` ждёт Approve → после Approve прод обновился.
- [ ] `systemctl list-timers | grep medarchive` — бэкап 03:00 и prod2stg 04:00 запланированы.
- [ ] В бакете — только шифрованные объекты; `medarchive-prod2stg` руками отрабатывает.
- [ ] Существующая PWA работает как раньше.
- [ ] Ключ rclone-crypt и пароли записаны в надёжном месте вне сервера.

## Если что-то пошло не так

| Симптом | Куда смотреть |
|---|---|
| Сервис не стартует | `journalctl -u medarchive-prod -n 50` (обычно — опечатка в `.env`) |
| Домен не открывается | `dig +short <домен>` → IP верный? `journalctl -u caddy -n 30` (сертификат требует DNS) |
| Actions деплой красный | лог джобы: SSH до `deploy@<IP_VPS>` проходит? секреты заданы? |
| Бэкап падает | прогнать `sudo -u medarchive /usr/local/bin/medarchive-backup` руками — ошибка будет в выводе |
| prod2stg падает | то же руками; частое — нет ни одного бэкапа в бакете |

Любой непонятный вывод — присылайте целиком, разберём.
