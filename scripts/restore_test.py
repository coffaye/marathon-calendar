"""Restore a backup into a temporary database and run application-level checks."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from validate_ics import validate_calendar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from marathon_calendar.ics import races_to_ics  # noqa: E402
from marathon_calendar.migrations import CURRENT_SCHEMA_VERSION  # noqa: E402
from marathon_calendar.repository import RaceStore  # noqa: E402


def restore_test(backup_path: str | Path) -> dict[str, object]:
    backup = Path(backup_path)
    with tempfile.TemporaryDirectory(prefix="marathon-calendar-restore-") as temp_dir:
        restored = Path(temp_dir) / "restored.db"
        with sqlite3.connect(backup) as source, sqlite3.connect(restored) as destination:
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        store = RaceStore(restored)
        races = store.list_races()
        calendar = races_to_ics(races, calendar_name="Restore Test", now=None)
        wire = validate_calendar(calendar)
        result = {
            "backup": str(backup),
            "integrity": integrity,
            "schema": store.schema_version(),
            "schema_current": store.schema_version() == CURRENT_SCHEMA_VERSION,
            "races": len(races),
            "calendar": wire,
        }
        if integrity != "ok" or not result["schema_current"]:
            raise RuntimeError(json.dumps(result, ensure_ascii=False))
        return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: restore_test.py BACKUP_DB")
    print(json.dumps(restore_test(sys.argv[1]), ensure_ascii=False, sort_keys=True))
