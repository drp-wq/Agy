#!/usr/bin/env bash
set -euo pipefail

DB_PATH="/root/multi-exchange-paper/data/paper_engine.sqlite3"
BACKUP_DIR="/root/multi-exchange-paper/data/backups"
mkdir -p "$BACKUP_DIR"

if [[ -f "$DB_PATH" ]]; then
  TS="$(date -u +%Y%m%dT%H%M%SZ)"
  /root/multi-exchange-paper/.venv/bin/python -c "
from pathlib import Path
from app.persistence.database import PaperDatabase
db = PaperDatabase(Path('$DB_PATH'))
db.backup_to(Path('$BACKUP_DIR/paper_engine_${TS}.sqlite3'))
db.close()
"
  ls -1t "$BACKUP_DIR"/paper_engine_*.sqlite3 | tail -n +15 | xargs -r rm -f
  echo "Backup completed: paper_engine_${TS}.sqlite3"
else
  echo "No database file at $DB_PATH yet."
fi
