"""Create a consistent SQLite backup and verify its integrity."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def backup(source_path: str | Path, destination_path: str | Path) -> dict[str, object]:
    source = Path(source_path)
    destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
        source_db.backup(destination_db)
        integrity = destination_db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        count = destination_db.execute("SELECT COUNT(*) FROM races").fetchone()[0]
    return {"source": str(source), "destination": str(destination), "integrity": integrity, "races": count}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: backup_database.py SOURCE_DB DESTINATION_DB")
    print(json.dumps(backup(sys.argv[1], sys.argv[2]), ensure_ascii=False, sort_keys=True))

