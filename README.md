# PUNKT

Telegram-бот и веб-реестр строительных замечаний. Реализация по
техническому заданию PUNKT v1.0 (20.09.2026): модульный монолит на
Django 5.2 — один Docker-образ, два процесса (веб и бот), общая
PostgreSQL.

## Состав

- `accounts` — пользователи, вход по персональной ссылке (magic link)
- `issues` — зоны, замечания, фото, история, бизнес-правила (`services.py`)
- `bot` — Telegram-адаптер (HTTPX) и диалог создания замечания
- `reports` — PDF-отчёт (ReportLab + встроенный шрифт DejaVu Sans)

## Локальный запуск (без Docker)

Требуется Python 3.12 и PostgreSQL 16 (либо оставить `DATABASE_URL`
пустым — тогда используется SQLite, только для разработки).

```bash
python -m venv venv
venv/Scripts/activate           # Windows; на Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # заполнить BOT_TOKEN и остальные значения
python manage.py migrate
python manage.py bootstrap_site
python manage.py createsuperuser   # технический администратор
python manage.py runserver
```

В отдельном терминале — бот:

```bash
python manage.py runbot
```

Дальше зайдите в `/admin`, добавьте зоны и участников (укажите им
`telegram_id`, `display_name`, `role`). Участник пишет боту `/web`,
получает ссылку и заходит на сайт.

Для быстрой проверки без Telegram есть `python scripts/seed_dev.py` —
создаёт объект, зону, трёх пользователей (инженер/исполнитель/
руководитель) и печатает одноразовые ссылки входа для каждого.

## Тесты

```bash
python manage.py test
```

Требует `.env` с `DEBUG=True` (см. выше) — при `DEBUG=False` статика
раздаётся через `ManifestStaticFilesStorage` и требует предварительного
`collectstatic` (так и происходит в Docker-образе при сборке).

48 тестов покрывают версионность (409 при несовпадении version),
идемпотентность по `request_id`, ограничение ролей, видимость карточек
исполнителем, ограничение «одно фото BEFORE», магическую ссылку входа
и весь диалог бота от `/new` до создания карточки.

## Запуск в Docker (продакшн)

```bash
cp .env.example .env   # заполнить реальными значениями, включая POSTGRES_*
docker compose up -d --build
docker compose exec web python manage.py bootstrap_site
docker compose exec web python manage.py createsuperuser
```

Compose поднимает `db` (PostgreSQL), `migrate` (разовый прогон
миграций), `web` (Gunicorn), `bot` (`runbot`) и `caddy` (HTTPS по
домену из `BASE_DOMAIN`, автоматический сертификат Let's Encrypt).

## Резервное копирование и восстановление

`scripts/backup.sh` — приостанавливает запись (`web`, `bot`), делает
`pg_dump` и архив `media`, возобновляет запись, шифрует (если задан
`BACKUP_GPG_RECIPIENT`) и копирует за пределы VPS (`BACKUP_REMOTE` —
назначение rsync). Ставится в cron на ежедневный запуск:

```
0 3 * * * BACKUP_PATH=/opt/punkt/backups /opt/punkt/scripts/backup.sh >> /var/log/punkt-backup.log 2>&1
```

Восстановление: `scripts/restore.sh punkt-<timestamp>.tar.gz[.gpg]`.
Проверьте процедуру восстановления до начала пилота (ТЗ §5.3: RPO ≤ 24 ч,
RTO ≤ 4 ч).

Очистка черновиков, файлов-сирот и истёкших токенов — тоже по
расписанию ОС, не самим приложением:

```
15 3 * * * cd /opt/punkt && docker compose exec -T web python manage.py cleanup
```

## Что не реализовано (сознательно, ТЗ §1)

PWA/офлайн-очередь, геолокация, чеклисты этапов, мобильное приложение,
несколько компаний, BIM, внешние деловые интеграции, ИИ, электронная
подпись, обязательная исполнительная документация, биллинг.

## Известные ограничения этой реализации

- Frontend — минимальный (server-rendered шаблоны + немного JS для
  fetch-запросов), без вылизанного дизайна: ТЗ явно требует «без
  отдельной сборки SPA».
- Rate-limit (≤5 токенов и ≤30 изменений в минуту) реализован на
  уровне блокировки строки `User` в БД — рабочий, но простой вариант;
  для нагрузки выше пилотной стоит вынести в отдельный слой.
- Тесты используют SQLite; частичный уникальный индекс (одно фото
  BEFORE) и `CheckConstraint` в SQLite тоже проверены, но `SET
  TRANSACTION ISOLATION LEVEL REPEATABLE READ` в PDF-отчёте применяется
  только на PostgreSQL (в продакшне) — на SQLite просто пропускается.
