from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


class SnapshotStore:
    """Daily raw snapshot storage with an explicit, bounded retention helper."""

    def __init__(self, root: str | Path = "data/snapshots", retention_days: int = 30):
        self.root = Path(root)
        self.retention_days = retention_days

    def _day_dir(self, source: str, observed_at: datetime) -> Path:
        day = observed_at.astimezone(timezone.utc).date().isoformat()
        return self.root / source / day

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def save_list(
        self,
        *,
        source: str,
        observed_at: datetime,
        pages: list[dict[str, Any]],
        manifest: dict[str, Any],
    ) -> Path:
        directory = self._day_dir(source, observed_at)
        self._write_json(directory / "list.json", {"pages": pages})
        self._write_json(directory / "manifest.json", manifest)
        return directory / "list.json"

    def save_details(
        self,
        *,
        source: str,
        observed_at: datetime,
        rows: Iterable[dict[str, Any]],
    ) -> Path:
        directory = self._day_dir(source, observed_at)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "details.ndjson"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return path

    def save_text(
        self,
        *,
        source: str,
        observed_at: datetime,
        filename: str,
        content: str,
        manifest: dict[str, Any] | None = None,
    ) -> Path:
        directory = self._day_dir(source, observed_at)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_text(content, encoding="utf-8", newline="\n")
        if manifest is not None:
            self._write_json(directory / "manifest.json", manifest)
        return path

    def save_json(
        self,
        *,
        source: str,
        observed_at: datetime,
        filename: str,
        value: Any,
    ) -> Path:
        directory = self._day_dir(source, observed_at)
        path = directory / filename
        self._write_json(path, value)
        return path

    def prune(self, *, source: str, today: date | None = None) -> list[Path]:
        """Keep the most recent retention_days date directories for one source."""

        source_root = self.root / source
        if not source_root.exists():
            return []
        cutoff = (today or datetime.now(timezone.utc).date()) - timedelta(days=self.retention_days - 1)
        removed: list[Path] = []
        for directory in source_root.iterdir():
            if not directory.is_dir():
                continue
            try:
                directory_date = date.fromisoformat(directory.name)
            except ValueError:
                continue
            if directory_date < cutoff:
                shutil.rmtree(directory)
                removed.append(directory)
        return removed
