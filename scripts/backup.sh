#!/usr/bin/env bash
# Ежедневная копия БД и media (ТЗ §5.3): приостановка записи → pg_dump и
# media → возобновление → шифрование → копия вне VPS → ротация 7 дней.
# Запускается планировщиком ОС (cron), не приложением.
#
# Требует в окружении (например, /etc/punkt-backup.env, подключаемый cron'ом):
#   COMPOSE_FILE, BACKUP_PATH, BACKUP_GPG_RECIPIENT, BACKUP_REMOTE (rsync-назначение)
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-/opt/punkt/docker-compose.yml}"
BACKUP_PATH="${BACKUP_PATH:-/opt/punkt/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORKDIR="${BACKUP_PATH}/tmp-${STAMP}"

mkdir -p "${WORKDIR}"
cleanup() { rm -rf "${WORKDIR}"; }
trap cleanup EXIT

compose() { docker compose -f "${COMPOSE_FILE}" "$@"; }

echo "[backup] приостановка записи (web, bot)"
compose stop web bot

echo "[backup] pg_dump"
compose exec -T db pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" \
  | gzip > "${WORKDIR}/db-${STAMP}.sql.gz"

echo "[backup] архив media"
compose run --rm -T --entrypoint sh web -c \
  "tar -czf - -C /app/media ." > "${WORKDIR}/media-${STAMP}.tar.gz"

echo "[backup] возобновление записи"
compose start web bot

ARCHIVE="${BACKUP_PATH}/punkt-${STAMP}.tar"
tar -cf "${ARCHIVE}" -C "${WORKDIR}" "db-${STAMP}.sql.gz" "media-${STAMP}.tar.gz"

if [ -n "${BACKUP_GPG_RECIPIENT:-}" ]; then
  echo "[backup] шифрование"
  gpg --yes --batch --recipient "${BACKUP_GPG_RECIPIENT}" --encrypt --output "${ARCHIVE}.gpg" "${ARCHIVE}"
  rm -f "${ARCHIVE}"
  ARCHIVE="${ARCHIVE}.gpg"
fi

if [ -n "${BACKUP_REMOTE:-}" ]; then
  echo "[backup] копия вне VPS: ${BACKUP_REMOTE}"
  rsync -a "${ARCHIVE}" "${BACKUP_REMOTE}/"
fi

echo "[backup] ротация локальных копий старше 7 дней"
find "${BACKUP_PATH}" -maxdepth 1 -name 'punkt-*.tar*' -mtime +7 -delete

echo "[backup] готово: ${ARCHIVE}"
