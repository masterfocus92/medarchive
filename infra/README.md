# Runbook: настройка VPS (этап 6.5, ADR-015/016/017)

Пошаговая установка релизного контура на VPS (Ubuntu 22.04, 1 ГБ RAM).
Выполняется root'ом один раз. Секреты нигде не коммитятся.
Процесс релиза после настройки — `RELEASE.md` в корне репозитория.

## 0. Что понадобится

- SSH-доступ root (или sudo) к VPS.
- Домен `mdkarta.online` в панели регистратора.
- Аккаунт Яндекс Облака (Object Storage) — бакет и статический ключ доступа.
- GitHub-репозиторий проекта (права admin — для Secrets и Environments).

## 1. База: swap, пользователи, каталоги (T6.5.1)

```bash
# Swap 2 ГБ (ADR-017)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Пользователи: medarchive — владелец приложения; deploy — только для CI-деплоя
useradd --system --create-home --shell /bin/bash medarchive
useradd --create-home --shell /bin/bash deploy

# Каталоги контуров и файлов
install -d -o medarchive -g medarchive /opt/medarchive /var/lib/medarchive/{prod,stg}/files /var/log/medarchive
chmod 750 /var/lib/medarchive/{prod,stg}/files

# uv (в /usr/local/bin — путь зашит в systemd-юнитах)
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
```

## 2. PostgreSQL 16 + pgvector нативно (T6.5.1, ADR-017)

```bash
# Репозиторий PGDG
apt install -y postgresql-common
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
apt install -y postgresql-16 postgresql-16-pgvector

# Тюнинг под 1 ГБ
cp infra/postgres/medarchive.conf /etc/postgresql/16/main/conf.d/
systemctl restart postgresql

# Роли и БД (пароли придумать и записать в .env контуров)
sudo -u postgres psql <<'SQL'
CREATE ROLE medcard_prod LOGIN PASSWORD 'CHANGE_ME_PROD';
CREATE ROLE medcard_stg  LOGIN PASSWORD 'CHANGE_ME_STG';
CREATE DATABASE medcard_prod OWNER medcard_prod;
CREATE DATABASE medcard_stg  OWNER medcard_stg;
SQL

# Пароль прод-БД для бэкапов (pg_dump от medarchive)
sudo -u medarchive bash -c 'echo "localhost:5432:medcard_prod:medcard_prod:CHANGE_ME_PROD" > ~/.pgpass && chmod 600 ~/.pgpass'
```

## 3. Контуры приложения (T6.5.1)

```bash
sudo -u medarchive git clone -b main https://github.com/<owner>/<repo>.git /opt/medarchive/prod
sudo -u medarchive git clone -b stg  https://github.com/<owner>/<repo>.git /opt/medarchive/stg
# (ветка stg создаётся на шаге 7, до того можно клонировать main)

# .env контуров — по шаблонам, заполнить CHANGE_ME (СВОИ SECRET_KEY и ключи AI!)
sudo -u medarchive cp /opt/medarchive/prod/infra/env.prod.example /opt/medarchive/prod/.env
sudo -u medarchive cp /opt/medarchive/stg/infra/env.stg.example  /opt/medarchive/stg/.env
sudo -u medarchive nano /opt/medarchive/prod/.env   # и stg

# Зависимости и миграции
sudo -u medarchive -H bash -c 'cd /opt/medarchive/prod && uv sync --frozen && uv run alembic upgrade head'
sudo -u medarchive -H bash -c 'cd /opt/medarchive/stg  && uv sync --frozen && uv run alembic upgrade head'

# Seed прод-семьи: .env.seed по шаблону .env.seed.example (реальные данные, вне git)
sudo -u medarchive nano /opt/medarchive/prod/.env.seed
sudo -u medarchive -H bash -c 'cd /opt/medarchive/prod && uv run python -m app.seed'

# systemd
cp infra/systemd/medarchive-{prod,stg}.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now medarchive-prod medarchive-stg
curl -fsS localhost:8000/health && curl -fsS localhost:8001/health   # оба {"status":"ok"}
```

## 4. Caddy и DNS (T6.5.2)

1. В панели регистратора: A-записи `mdkarta.online` и `stg.mdkarta.online` → IP VPS.
2. Дописать блоки из `infra/caddy/Caddyfile.medarchive` в `/etc/caddy/Caddyfile`
   (существующие сайты не трогать) и `systemctl reload caddy`.
3. Проверка: оба домена открываются по HTTPS, существующая PWA работает.
4. Firewall: наружу только 22/80/443 (`ufw allow 22,80,443/tcp && ufw enable`);
   8000/8001 слушают 127.0.0.1 — снаружи недоступны и без ufw.

## 5. Бэкапы (T6.5.5, ADR-016)

1. В Яндекс Облаке: бакет (например `mdkarta-backup`), сервисный аккаунт
   со статическим ключом (только этот бакет).
2. rclone на VPS: `apt install -y rclone`, затем от medarchive —
   `sudo -u medarchive rclone config`:
   - remote `yandex`: тип `s3`, provider `Other`, endpoint `storage.yandexcloud.net`,
     ключи из п.1;
   - remote `medarchive-crypt`: тип `crypt`, remote `yandex:mdkarta-backup`,
     пароль шифрования — сгенерировать и **сохранить оффлайн-копию**
     (без него бэкапы нечитаемы; это критический секрет).
3. Установка скриптов и таймеров:

```bash
cp /opt/medarchive/prod/infra/backup.sh   /usr/local/bin/medarchive-backup   && chmod 755 /usr/local/bin/medarchive-backup
cp /opt/medarchive/prod/infra/prod2stg.sh /usr/local/bin/medarchive-prod2stg && chmod 755 /usr/local/bin/medarchive-prod2stg
cp /opt/medarchive/prod/infra/systemd/medarchive-{backup,prod2stg}.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now medarchive-backup.timer medarchive-prod2stg.timer

# Первый прогон руками + проверка
sudo -u medarchive /usr/local/bin/medarchive-backup
rclone lsf medarchive-crypt:daily/          # дамп и архив на месте
rclone lsf yandex:mdkarta-backup            # в бакете — только шифрованные имена
/usr/local/bin/medarchive-prod2stg          # стенд пересобрался из бэкапа
```

> Скрипты скопированы в `/usr/local/bin` осознанно: деплой обновляет код
> контуров, но не системные скрипты. При изменении скриптов в репо —
> повторить `cp` (шаг фиксируется в RELEASE.md).

## 6. Деплой и CI (T6.5.4, T6.5.7)

```bash
cp /opt/medarchive/prod/infra/deploy.sh /usr/local/bin/medarchive-deploy && chmod 755 /usr/local/bin/medarchive-deploy

# deploy-пользователь: ключ для CI и право ровно на одну команду
sudo -u deploy ssh-keygen -t ed25519 -N '' -f /home/deploy/.ssh/id_ci
cat /home/deploy/.ssh/id_ci.pub >> /home/deploy/.ssh/authorized_keys
echo 'deploy ALL=(root) NOPASSWD: /usr/local/bin/medarchive-deploy' > /etc/sudoers.d/medarchive-deploy
chmod 440 /etc/sudoers.d/medarchive-deploy
```

В GitHub (Settings репозитория):
1. **Secrets and variables → Actions**: `DEPLOY_HOST` = IP/домен VPS,
   `DEPLOY_SSH_KEY` = содержимое `/home/deploy/.ssh/id_ci` (приватный ключ;
   после добавления удалить с сервера: `rm /home/deploy/.ssh/id_ci`).
2. **Environments → New**: `production`, Required reviewers → владелец.
3. **Branches → Protection** для `main`: require PR (merge только из `stg`),
   require status checks (tests).
4. Создать ветку `stg`: `git branch stg main && git push -u origin stg`.

## 7. Финальная проверка контура

- [ ] `https://mdkarta.online` и `https://stg.mdkarta.online` открываются, вход работает.
- [ ] На стенде видна плашка «Тестовый стенд», на проде — нет.
- [ ] Пуш в `stg` → Actions зелёные → стенд обновился сам.
- [ ] Merge `stg`→`main` → job ждёт Approve → после Approve прод обновился.
- [ ] `systemctl list-timers | grep medarchive` — оба таймера запланированы.
- [ ] В бакете лежит шифрованный бэкап; `prod2stg` руками отрабатывает.
- [ ] Существующая PWA владельца работает как раньше.
