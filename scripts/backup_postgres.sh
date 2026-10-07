#!/usr/bin/env bash
# PostgreSQL backup for the production Compose stack.
#
# Usage (from the repository root, on the server):
#   ./scripts/backup_postgres.sh
#
# Writes a timestamped custom-format dump to BACKUP_DIR (default
# /var/backups/svoi-pravila), which must be outside the Docker volume.
# Credentials are never passed on the command line: pg_dump runs inside the
# postgres container and uses that container's own POSTGRES_USER/POSTGRES_DB.
# Dumps older than RETENTION_DAYS (default 14) are removed from BACKUP_DIR.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/svoi-pravila}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

umask 077
mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/svoi_pravila_${stamp}.dump"

"${COMPOSE[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$target.partial"
mv "$target.partial" "$target"

find "$BACKUP_DIR" -maxdepth 1 -name 'svoi_pravila_*.dump' -type f -mtime +"$RETENTION_DAYS" -delete

echo "Backup written: $target"
