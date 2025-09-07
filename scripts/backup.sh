#!/usr/bin/env bash
set -euo pipefail

# Simple DB backup helper for SQLite and Postgres
# Usage:
#   DATABASE_URL=sqlite:////workspace/data/app.db ./scripts/backup.sh
#   DATABASE_URL=postgresql://user:pass@host:5432/dbname ./scripts/backup.sh

TS="$(date +%Y%m%d-%H%M%S)"
OUTDIR="${BACKUP_DIR:-backups}"
mkdir -p "$OUTDIR"

DB_URL="${DATABASE_URL:-}"
if [ -z "$DB_URL" ]; then
  echo "ERROR: DATABASE_URL not set" >&2
  exit 1
fi

case "$DB_URL" in
  sqlite:*|sqlite3:*)
    # Expect format sqlite:////absolute/path/to.db
    # strip scheme
    FILE_PATH="${DB_URL#sqlite://}"
    FILE_PATH="${FILE_PATH#sqlite3://}"
    # if single leading slash was removed incorrectly, normalize
    FILE_PATH="${FILE_PATH/#\//\/}"
    BASENAME="$(basename "$FILE_PATH")"
    cp -a "$FILE_PATH" "$OUTDIR/${BASENAME%.db}_${TS}.db"
    echo "SQLite backup created: $OUTDIR/${BASENAME%.db}_${TS}.db"
    ;;

  postgresql:*|postgres:*)
    # Needs pg_dump in PATH
    NAME="pg_backup_${TS}.sql.gz"
    pg_dump "$DB_URL" | gzip -9 > "$OUTDIR/$NAME"
    echo "PostgreSQL backup created: $OUTDIR/$NAME"
    ;;

  *)
    echo "ERROR: Unsupported DATABASE_URL scheme: $DB_URL" >&2
    exit 2
    ;;
esac