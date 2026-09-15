"""Deterministic, human-readable persistence for canonical calendar state.

SQLite remains the working database. This module is the durable Git-friendly
representation used by the GitHub Actions pipeline. Raw HTTP payloads and
local snapshot paths are deliberately excluded; they belong in short-lived
artifacts, not in the canonical state commit.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .domain.models import (
    Race,
    RaceAlias,
    RaceChange,
    RaceFieldOverride,
    RaceSource,
    ReconciliationIssue,
    SourceDiscrepancy,
    UnresolvedSourceRecord,
)
from .migrations import CURRENT_SCHEMA_VERSION
from .repository import RaceStore, _dump


STATE_FORMAT_VERSION = 1
STATE_FILES = (
    "canonical_races.json",
    "source_links.json",
    "aliases.json",
    "manual_overrides.json",
    "reconciliation.json",
    "history.json",
    "metadata.json",
)


def _model_dict(model: Any, *, omit: Iterable[str] = ()) -> dict[str, Any]:
    value = model.model_dump(mode="json")
    for key in omit:
        value.pop(key, None)
    return value


def _state_race(race: Race) -> dict[str, Any]:
    # These are runner-observation timestamps. Calendar history is carried by
    # last_modified/sequence and RaceChange rows; persisting these would make
    # an unchanged daily sync dirty the canonical state.
    return _model_dict(race, omit=("updated_at", "last_verified_at"))


def _state_source(source: RaceSource) -> dict[str, Any]:
    # A source link is canonical mapping metadata, not an archive of the raw
    # response. The latter is too large and belongs in an Actions artifact.
    document = source.source_document if (source.source_document or "").startswith(("http://", "https://")) else None
    return _model_dict(
        source,
        omit=(
            "raw_data",
            "source_document",
            "fetched_at",
            "retrieved_at",
            "last_confirmed_at",
            "verified_at",
            "last_seen_at",
        ),
    ) | {
        "raw_data": {},
        "source_document": document,
    }


def _state_unresolved(record: UnresolvedSourceRecord) -> dict[str, Any]:
    # last_seen_at is runner-observation metadata. Keeping it in the committed
    # state would create a daily diff for an unchanged unresolved record.
    return _model_dict(record, omit=("raw_data", "last_seen_at")) | {"raw_data": {}}


def _sort_records(records: Iterable[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
    return sorted(records, key=lambda record: tuple(str(record.get(key, "")) for key in keys))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_production_safe(store: RaceStore) -> None:
    """Refuse to serialize synthetic fixtures in a production build."""

    if os.environ.get("MARATHON_CALENDAR_ENV", "development").strip().casefold() != "production":
        return
    fixture_sources = [
        source
        for source in store.list_sources()
        if source.source_type == "fixture" or source.raw_data.get("fixture") is True
    ]
    if fixture_sources:
        raise RuntimeError(f"production state contains {len(fixture_sources)} fixture source records")


def export_state(store: RaceStore, output_dir: str | Path) -> dict[str, Any]:
    assert_production_safe(store)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    races = _sort_records((_state_race(item) for item in store.list_all_races()), "id")
    sources = _sort_records((_state_source(item) for item in store.list_sources()), "source_name", "external_id", "id")
    aliases = _sort_records((_model_dict(item) for item in store.list_aliases()), "race_id", "normalized_name", "id")
    overrides = _sort_records((_model_dict(item) for item in store.list_all_overrides()), "race_id", "field_name", "created_at", "id")
    issues = _sort_records((_model_dict(item) for item in store.list_issues()), "source_name", "external_id", "id")
    unresolved = _sort_records((_state_unresolved(item) for item in store.list_unresolved_source_records()), "source_name", "external_id", "id")
    discrepancies = _sort_records((_model_dict(item) for item in store.list_discrepancies()), "race_id", "field_name", "source_id", "id")
    changes = _sort_records((_model_dict(item) for item in store.list_changes()), "race_id", "changed_at", "id")
    _write_json(output / "canonical_races.json", races)
    _write_json(output / "source_links.json", sources)
    _write_json(output / "aliases.json", aliases)
    _write_json(output / "manual_overrides.json", overrides)
    _write_json(
        output / "reconciliation.json",
        {"issues": issues, "unresolved_source_records": unresolved, "discrepancies": discrepancies},
    )
    _write_json(output / "history.json", changes)
    metadata = {
        "format_version": STATE_FORMAT_VERSION,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "state_files": list(STATE_FILES),
        "policy": {
            "deterministic": True,
            "raw_source_payloads_included": False,
            "runner_paths_included": False,
        },
        "record_counts": {
            "races": len(races),
            "active_races": sum(1 for race in races if race.get("merged_into_id") is None),
            "source_links": len(sources),
            "aliases": len(aliases),
            "manual_overrides": len(overrides),
            "reconciliation_issues": len(issues),
            "unresolved_source_records": len(unresolved),
            "discrepancies": len(discrepancies),
            "race_changes": len(changes),
        },
    }
    _write_json(output / "metadata.json", metadata)
    return metadata


def _require_list(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{name} must be a JSON array of objects")
    return value


def _load_state(state_dir: str | Path) -> dict[str, Any]:
    state = Path(state_dir)
    metadata = _read_json(state / "metadata.json")
    if metadata.get("format_version") != STATE_FORMAT_VERSION:
        raise ValueError(f"unsupported state format: {metadata.get('format_version')!r}")
    if metadata.get("schema_version") != CURRENT_SCHEMA_VERSION:
        raise ValueError("state schema does not match the current migration version")
    reconciliation = _read_json(state / "reconciliation.json")
    loaded = {
        "metadata": metadata,
        "races": _require_list(_read_json(state / "canonical_races.json"), "canonical_races"),
        "sources": _require_list(_read_json(state / "source_links.json"), "source_links"),
        "aliases": _require_list(_read_json(state / "aliases.json"), "aliases"),
        "overrides": _require_list(_read_json(state / "manual_overrides.json"), "manual_overrides"),
        "issues": _require_list(reconciliation.get("issues"), "reconciliation.issues"),
        "unresolved": _require_list(reconciliation.get("unresolved_source_records"), "reconciliation.unresolved_source_records"),
        "discrepancies": _require_list(reconciliation.get("discrepancies"), "reconciliation.discrepancies"),
        "changes": _require_list(_read_json(state / "history.json"), "history"),
    }
    expected_counts = {
        "races": len(loaded["races"]),
        "source_links": len(loaded["sources"]),
        "aliases": len(loaded["aliases"]),
        "manual_overrides": len(loaded["overrides"]),
        "reconciliation_issues": len(loaded["issues"]),
        "unresolved_source_records": len(loaded["unresolved"]),
        "discrepancies": len(loaded["discrepancies"]),
        "race_changes": len(loaded["changes"]),
    }
    recorded_counts = metadata.get("record_counts", {})
    for key, actual in expected_counts.items():
        if recorded_counts.get(key) != actual:
            raise ValueError(f"state count mismatch for {key}: {recorded_counts.get(key)!r} != {actual}")
    active_races = sum(1 for race in loaded["races"] if race.get("merged_into_id") is None)
    if recorded_counts.get("active_races") != active_races:
        raise ValueError(f"state count mismatch for active_races: {recorded_counts.get('active_races')!r} != {active_races}")
    return loaded


def _insert_override(connection: sqlite3.Connection, override: RaceFieldOverride) -> None:
    connection.execute(
        "INSERT INTO race_field_overrides (id, race_id, payload, field_name, active) VALUES (?, ?, ?, ?, ?)",
        (str(override.id), str(override.race_id), _dump(override), override.field_name, int(override.active)),
    )


def _insert_issue(connection: sqlite3.Connection, issue: ReconciliationIssue) -> None:
    connection.execute(
        "INSERT INTO reconciliation_issues (id, payload, source_name, external_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (str(issue.id), _dump(issue), issue.source_name, issue.external_id, issue.status.value, issue.created_at.isoformat()),
    )


def import_state(
    state_dir: str | Path,
    db_path: str | Path,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Import state into a fresh/empty runtime DB.

    ``replace=True`` is an explicit operator action for rebuilding a known
    target path. The normal workflow uses a new temporary DB and leaves the
    previous runtime DB untouched until all validation gates pass.
    """

    state = _load_state(state_dir)
    target = Path(db_path)
    if replace and target.exists():
        target.unlink()
        for sidecar in target.parent.glob(target.name + "-*"):
            sidecar.unlink()
    store = RaceStore(target)
    if store.count() and not replace:
        raise ValueError(f"target database is not empty: {target}")

    with store._connect() as connection:
        for raw in state["races"]:
            RaceStore._save_race(connection, Race.model_validate(raw))
        for raw in state["sources"]:
            RaceStore._save_source(connection, RaceSource.model_validate(raw))
        for raw in state["aliases"]:
            alias = RaceAlias.model_validate(raw)
            connection.execute(
                "INSERT INTO race_aliases (id, race_id, payload, normalized_name, source_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(alias.id), str(alias.race_id), _dump(alias), alias.normalized_name, str(alias.source_id) if alias.source_id else None, alias.created_at.isoformat()),
            )
        for raw in state["overrides"]:
            _insert_override(connection, RaceFieldOverride.model_validate(raw))
        for raw in state["issues"]:
            _insert_issue(connection, ReconciliationIssue.model_validate(raw))
        for raw in state["unresolved"]:
            record = UnresolvedSourceRecord.model_validate(raw)
            connection.execute(
                "INSERT INTO unresolved_source_records (id, payload, source_name, external_id, source_year, last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(record.id), _dump(record), record.source_name, record.external_id, record.source_year, record.last_seen_at.isoformat()),
            )
        for raw in state["discrepancies"]:
            discrepancy = SourceDiscrepancy.model_validate(raw)
            connection.execute(
                "INSERT INTO source_discrepancies (id, payload, race_id, source_id, source_name, field_name, detected_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(discrepancy.id), _dump(discrepancy), str(discrepancy.race_id), str(discrepancy.source_id), discrepancy.source_name, discrepancy.field_name, discrepancy.detected_at.isoformat()),
            )
        for raw in state["changes"]:
            RaceStore._save_change(connection, RaceChange.model_validate(raw))

    return export_state(store, state_dir)
