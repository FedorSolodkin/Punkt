#!/usr/bin/env bash
# Восстановление из копии, сделанной scripts/backup.sh (ТЗ §5.3, RTO ≤ 4 ч).
# Использование: scripts/restore.sh punkt-20260101T000000Z.tar[.gpg]
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-/opt/punkt/docker-compose.yml}"
BACKUP_PATH="${BACKUP_PATH:-/opt/punkt/backups}"
ARCHIVE_NAME="${1:?Укажите имя файла копии в ${BACKUP_PATH}}"
ARCHIVE="${BACKUP_PATH}/${ARCHIVE_NAME}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

compose() { docker compose -f "${COMPOSE_FILE}" "$@"; }

if [[ "${ARCHIVE}" == *.gpg ]]; then
  echo "[restore] расшифровка"
  gpg --yes --batch --decrypt --output "${WORKDIR}/plain.tar" "${ARCHIVE}"
  TAR_FILE="${WORKDIR}/plain.tar"
else
  TAR_FILE="${ARCHIVE}"
fi

tar -xf "${TAR_FILE}" -C "${WORKDIR}"
DB_DUMP="$(ls "${WORKDIR}"/db-*.sql.gz)"
MEDIA_TAR="$(ls "${WORKDIR}"/media-*.tar.gz)"

echo "[restore] остановка веба и бота"
compose stop web bot

echo "[restore] восстановление БД (перезапись!)"
gunzip -c "${DB_DUMP}" | compose exec -T db psql -U "${POSTGRES_USER}" "${POSTGRES_DB}"

echo "[restore] восстановление media"
compose run --rm -T --entrypoint sh web -c \
  "rm -rf /app/media/* && tar -xzf - -C /app/media" < "${MEDIA_TAR}"

echo "[restore] запуск веба и бота"
compose start web bot

echo "[restore] готово. Проверьте /issues и создание тестового замечания."
