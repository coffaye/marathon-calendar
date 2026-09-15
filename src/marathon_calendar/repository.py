from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

from .domain.models import (
    Race,
    RaceAlias,
    RaceChange,
    RaceFieldOverride,
    RaceSource,
    ReconciliationIssue,
    SourceRole,
    SourceDiscrepancy,
    SyncRun,
    UnresolvedSourceRecord,
    utc_now,
)
from .authority import ROLE_RANK, highest_current_source, is_current_publishable_source, normalize_source_semantics, verification_for_race
from .migrations import CURRENT_SCHEMA_VERSION, MIGRATIONS


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _dump(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, default=_json_default)


class RaceStore:
    """SQLite repository; domain and source adapters do not know SQL details."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            # WAL lets feed readers overlap with the short write transactions
            # used by sync and keeps a stalled reader from blocking a writer.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            current = int(connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])
            for version, sql in MIGRATIONS:
                if version > current:
                    connection.executescript(sql)
                    connection.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, utc_now().isoformat()),
                    )
        self._backfill_source_semantics()

    def schema_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
        return int(row["version"])

    def is_schema_current(self) -> bool:
        return self.schema_version() == CURRENT_SCHEMA_VERSION

    def _backfill_source_semantics(self) -> None:
        """Upgrade legacy Phase 2 payloads to explicit role/freshness semantics."""

        with self._connect() as connection:
            source_rows = connection.execute("SELECT id, payload FROM race_sources").fetchall()
            sources_by_race: dict[str, list[RaceSource]] = {}
            for row in source_rows:
                source = normalize_source_semantics(RaceSource.model_validate(json.loads(row["payload"])))
                sources_by_race.setdefault(str(source.race_id), []).append(source)
                self._save_source(connection, source)
            race_rows = connection.execute("SELECT payload FROM races").fetchall()
            for row in race_rows:
                race = Race.model_validate(json.loads(row["payload"]))
                sources = sources_by_race.get(str(race.id), [])
                current = highest_current_source(sources)
                # This hook is also run every time a process opens the DB.
                # Once Phase 2+ has persisted an explicit confirmation source,
                # reopening the DB must not silently choose a different tied
                # source and change canonical state/UID-adjacent metadata.
                verification = (
                    verification_for_race(race, sources)
                    if current is not None
                    and race.confirmed_by_source_id is None
                    and race.verification_status.value == "needs_verification"
                    else race.verification_status
                )
                self._save_race(
                    connection,
                    race.model_copy(
                        update={
                            "verification_status": verification,
                            "last_confirmed_at": race.last_confirmed_at or (current.last_confirmed_at if current else None),
                            "confirmed_by_source_id": race.confirmed_by_source_id or (current.id if current else None),
                        }
                    ),
                )

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM races").fetchone()[0])

    @staticmethod
    def _save_race(connection: sqlite3.Connection, race: Race) -> None:
        connection.execute(
            """
            INSERT INTO races
                (id, payload, canonical_identity_key, race_date, country, status,
                 verification_status, sequence, updated_at, last_confirmed_at, confirmed_by_source_id,
                 merged_into_id, merged_at, merge_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload,
                canonical_identity_key=excluded.canonical_identity_key,
                race_date=excluded.race_date,
                country=excluded.country,
                status=excluded.status,
                verification_status=excluded.verification_status,
                sequence=excluded.sequence,
                updated_at=excluded.updated_at,
                last_confirmed_at=excluded.last_confirmed_at,
                confirmed_by_source_id=excluded.confirmed_by_source_id,
                merged_into_id=excluded.merged_into_id,
                merged_at=excluded.merged_at,
                merge_reason=excluded.merge_reason
            """,
            (
                str(race.id),
                _dump(race),
                race.canonical_identity_key,
                race.race_date.isoformat(),
                race.country,
                race.status.value,
                race.verification_status.value,
                race.sequence,
                race.updated_at.isoformat(),
                race.last_confirmed_at.isoformat() if race.last_confirmed_at else None,
                str(race.confirmed_by_source_id) if race.confirmed_by_source_id else None,
                str(race.merged_into_id) if race.merged_into_id else None,
                race.merged_at.isoformat() if race.merged_at else None,
                race.merge_reason,
            ),
        )

    def save_race(self, race: Race) -> Race:
        with self._connect() as connection:
            self._save_race(connection, race)
        return race

    def get_race(self, race_id: UUID) -> Race | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM races WHERE id = ?", (str(race_id),)).fetchone()
        return Race.model_validate(json.loads(row["payload"])) if row else None

    def find_races_by_identity(self, identity_key: str) -> list[Race]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM races WHERE canonical_identity_key = ? AND merged_into_id IS NULL ORDER BY id", (identity_key,)
            ).fetchall()
        return [Race.model_validate(json.loads(row["payload"])) for row in rows]

    def list_races(self, *, country: str | None = None, full_marathon_only: bool = False) -> list[Race]:
        with self._connect() as connection:
            if country:
                rows = connection.execute(
                    "SELECT payload FROM races WHERE country = ? AND merged_into_id IS NULL ORDER BY race_date, id", (country,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT payload FROM races WHERE merged_into_id IS NULL ORDER BY race_date, id").fetchall()
        races = [Race.model_validate(json.loads(row["payload"])) for row in rows]
        if full_marathon_only:
            races = [
                race
                for race in races
                if any(value.casefold() in {"marathon", "全程", "full marathon"} for value in race.distance_types)
            ]
        return races

    def list_all_races(self) -> list[Race]:
        """Return active and soft-merged races for state serialization."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM races ORDER BY id").fetchall()
        return [Race.model_validate(json.loads(row["payload"])) for row in rows]

    def update_race(self, race_id: UUID, **changes: Any) -> Race:
        current = self.get_race(race_id)
        if current is None:
            raise KeyError(f"Race not found: {race_id}")
        candidate_values = current.model_dump(mode="python")
        candidate_values.update(changes)
        candidate = Race.model_validate(candidate_values)
        meaningful_fields = {
            "name",
            "name_en",
            "race_date",
            "start_time",
            "timezone",
            "province",
            "city",
            "status",
            "distance_types",
            "official_url",
            "organization",
            "association_level",
            "world_athletics_label",
        }
        changed = any(
            getattr(current, field) != getattr(candidate, field)
            for field in meaningful_fields
            if field in changes
        )
        now = utc_now()
        if changed:
            if now <= current.last_modified:
                now = current.last_modified + timedelta(seconds=1)
            candidate = candidate.model_copy(update={"sequence": current.sequence + 1, "last_modified": now})
        candidate = candidate.model_copy(update={"updated_at": now})
        return self.save_race(candidate)

    @staticmethod
    def _save_source(connection: sqlite3.Connection, source: RaceSource) -> None:
        source = normalize_source_semantics(source)
        connection.execute(
            """
            INSERT INTO race_sources
                (id, race_id, payload, external_id, is_authoritative, source_name, source_type,
                 source_hash, source_year, source_document, source_document_checksum,
                 source_row_number, source_publication_date, source_role, publishable,
                 published_at, retrieved_at, last_confirmed_at, last_seen_at, missing_count, flag_for_review)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                race_id=excluded.race_id,
                payload=excluded.payload,
                external_id=excluded.external_id,
                is_authoritative=excluded.is_authoritative,
                source_name=excluded.source_name,
                source_type=excluded.source_type,
                source_hash=excluded.source_hash,
                source_year=excluded.source_year,
                source_document=excluded.source_document,
                source_document_checksum=excluded.source_document_checksum,
                source_row_number=excluded.source_row_number,
                source_publication_date=excluded.source_publication_date,
                source_role=excluded.source_role,
                publishable=excluded.publishable,
                published_at=excluded.published_at,
                retrieved_at=excluded.retrieved_at,
                last_confirmed_at=excluded.last_confirmed_at,
                last_seen_at=excluded.last_seen_at,
                missing_count=excluded.missing_count,
                flag_for_review=excluded.flag_for_review
            """,
            (
                str(source.id),
                str(source.race_id),
                _dump(source),
                source.external_id,
                int(source.is_authoritative),
                source.source_name,
                source.source_type,
                source.source_hash,
                source.source_year,
                source.source_document,
                source.source_document_checksum,
                source.source_row_number,
                source.source_publication_date.isoformat() if source.source_publication_date else None,
                source.source_role.value,
                int(source.publishable),
                source.published_at.isoformat() if source.published_at else None,
                source.retrieved_at.isoformat() if source.retrieved_at else None,
                source.last_confirmed_at.isoformat() if source.last_confirmed_at else None,
                source.last_seen_at.isoformat() if source.last_seen_at else None,
                source.missing_count,
                int(source.flag_for_review),
            ),
        )

    def add_source(self, source: RaceSource) -> RaceSource:
        if self.get_race(source.race_id) is None:
            raise KeyError(f"Race not found: {source.race_id}")
        with self._connect() as connection:
            self._save_source_with_authority(connection, source)
        return source

    def _save_source_with_authority(self, connection: sqlite3.Connection, source: RaceSource) -> None:
        if source.is_authoritative:
            previous = connection.execute(
                "SELECT id, payload FROM race_sources WHERE race_id = ? AND id != ?",
                (str(source.race_id), str(source.id)),
            ).fetchall()
            for row in previous:
                previous_payload = json.loads(row["payload"])
                previous_payload["is_authoritative"] = False
                connection.execute(
                    "UPDATE race_sources SET payload = ?, is_authoritative = 0 WHERE id = ?",
                    (json.dumps(previous_payload, ensure_ascii=False), row["id"]),
                )
        self._save_source(connection, source)

    def save_source_and_race(
        self, race: Race, source: RaceSource, changes: Iterable[RaceChange] = ()
    ) -> None:
        """Persist source, effective race, and audit rows in one transaction."""

        with self._connect() as connection:
            self._save_race(connection, race)
            self._save_source_with_authority(connection, source)
            for change in changes:
                self._save_change(connection, change)

    def get_source_by_external(self, source_name: str, external_id: str) -> RaceSource | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM race_sources WHERE source_name = ? AND external_id = ? LIMIT 1",
                (source_name, external_id),
            ).fetchone()
        return normalize_source_semantics(RaceSource.model_validate(json.loads(row["payload"]))) if row else None

    def list_sources(self, race_id: UUID | None = None) -> list[RaceSource]:
        with self._connect() as connection:
            if race_id:
                rows = connection.execute(
                    "SELECT payload FROM race_sources WHERE race_id = ? ORDER BY id", (str(race_id),)
                ).fetchall()
            else:
                rows = connection.execute("SELECT payload FROM race_sources ORDER BY id").fetchall()
        return [normalize_source_semantics(RaceSource.model_validate(json.loads(row["payload"]))) for row in rows]

    def list_sources_for_source(self, source_name: str) -> list[RaceSource]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM race_sources WHERE source_name = ? ORDER BY id", (source_name,)
            ).fetchall()
        return [normalize_source_semantics(RaceSource.model_validate(json.loads(row["payload"]))) for row in rows]

    def list_sources_by_role(self, source_role: SourceRole) -> list[RaceSource]:
        return [source for source in self.list_sources() if source.source_role is source_role]

    def add_alias(self, alias: RaceAlias) -> RaceAlias:
        if self.get_race(alias.race_id) is None:
            raise KeyError(f"Race not found: {alias.race_id}")
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1
                FROM race_aliases
                WHERE race_id = ?
                  AND normalized_name = ?
                  AND (
                    source_id = ?
                    OR (source_id IS NULL AND ? IS NULL)
                  )
                LIMIT 1
                """,
                (
                    str(alias.race_id),
                    alias.normalized_name,
                    str(alias.source_id) if alias.source_id else None,
                    str(alias.source_id) if alias.source_id else None,
                ),
            ).fetchone()
            if existing is not None:
                return alias
            connection.execute(
                """
                INSERT INTO race_aliases (id, race_id, payload, normalized_name, source_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, normalized_name=excluded.normalized_name,
                    source_id=excluded.source_id
                """,
                (str(alias.id), str(alias.race_id), _dump(alias), alias.normalized_name,
                 str(alias.source_id) if alias.source_id else None, alias.created_at.isoformat()),
            )
        return alias

    def list_aliases(self, race_id: UUID | None = None) -> list[RaceAlias]:
        with self._connect() as connection:
            if race_id:
                rows = connection.execute(
                    "SELECT payload FROM race_aliases WHERE race_id = ? ORDER BY created_at, id", (str(race_id),)
                ).fetchall()
            else:
                rows = connection.execute("SELECT payload FROM race_aliases ORDER BY created_at, id").fetchall()
        return [RaceAlias.model_validate(json.loads(row["payload"])) for row in rows]

    def list_all_overrides(self) -> list[RaceFieldOverride]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM race_field_overrides ORDER BY id"
            ).fetchall()
        return [RaceFieldOverride.model_validate(json.loads(row["payload"])) for row in rows]

    def save_unresolved_source_record(self, record: UnresolvedSourceRecord) -> UnresolvedSourceRecord:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload FROM unresolved_source_records WHERE source_name = ? AND external_id = ?",
                (record.source_name, record.external_id),
            ).fetchone()
            payload = record.model_dump(mode="json")
            if existing is not None:
                # Keep the original audit identity and first observation while
                # allowing the latest source payload/reason to be refreshed.
                previous = json.loads(existing["payload"])
                payload["id"] = previous.get("id", payload["id"])
                payload["first_seen_at"] = previous.get("first_seen_at", payload["first_seen_at"])
                connection.execute(
                    """
                    UPDATE unresolved_source_records
                    SET payload = ?, source_year = ?, last_seen_at = ?
                    WHERE source_name = ? AND external_id = ?
                    """,
                    (
                        json.dumps(payload, ensure_ascii=False),
                        record.source_year,
                        record.last_seen_at.isoformat(),
                        record.source_name,
                        record.external_id,
                    ),
                )
                return UnresolvedSourceRecord.model_validate(payload)
            connection.execute(
                """
                INSERT INTO unresolved_source_records
                    (id, payload, source_name, external_id, source_year, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_name, external_id) DO UPDATE SET
                    payload=excluded.payload, source_year=excluded.source_year, last_seen_at=excluded.last_seen_at
                """,
                (str(record.id), _dump(record), record.source_name, record.external_id, record.source_year,
                 record.last_seen_at.isoformat()),
            )
        return record

    def list_unresolved_source_records(self, source_name: str | None = None) -> list[UnresolvedSourceRecord]:
        with self._connect() as connection:
            if source_name:
                rows = connection.execute(
                    "SELECT payload FROM unresolved_source_records WHERE source_name = ? ORDER BY source_name, external_id",
                    (source_name,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM unresolved_source_records ORDER BY source_name, external_id"
                ).fetchall()
        return [UnresolvedSourceRecord.model_validate(json.loads(row["payload"])) for row in rows]

    def resolve_unresolved_source_record(self, source_name: str, external_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM unresolved_source_records WHERE source_name = ? AND external_id = ?",
                (source_name, external_id),
            )

    def save_discrepancy(self, discrepancy: SourceDiscrepancy) -> SourceDiscrepancy:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload FROM source_discrepancies WHERE id = ?",
                (str(discrepancy.id),),
            ).fetchone()
            payload = discrepancy.model_dump(mode="json")
            if existing is not None:
                # The deterministic discrepancy ID represents the same
                # source-vs-canonical observation. Preserve its first
                # detection time across repeated source replays.
                previous = json.loads(existing["payload"])
                payload["detected_at"] = previous.get("detected_at", payload["detected_at"])
                connection.execute(
                    "UPDATE source_discrepancies SET payload = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), str(discrepancy.id)),
                )
                return SourceDiscrepancy.model_validate(payload)
            connection.execute(
                """
                INSERT INTO source_discrepancies
                    (id, payload, race_id, source_id, source_name, field_name, detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload
                """,
                (str(discrepancy.id), _dump(discrepancy), str(discrepancy.race_id), str(discrepancy.source_id),
                 discrepancy.source_name, discrepancy.field_name, discrepancy.detected_at.isoformat()),
            )
        return discrepancy

    def list_discrepancies(self, race_id: UUID | None = None) -> list[SourceDiscrepancy]:
        with self._connect() as connection:
            if race_id:
                rows = connection.execute(
                    "SELECT payload FROM source_discrepancies WHERE race_id = ? ORDER BY detected_at, id",
                    (str(race_id),),
                ).fetchall()
            else:
                rows = connection.execute("SELECT payload FROM source_discrepancies ORDER BY detected_at, id").fetchall()
        return [SourceDiscrepancy.model_validate(json.loads(row["payload"])) for row in rows]

    def merge_races(self, survivor_id: UUID, loser_id: UUID, *, reason: str) -> Race:
        """Soft-merge a duplicate while keeping its history and source records."""

        if survivor_id == loser_id:
            raise ValueError("survivor and loser must differ")
        survivor = self.get_race(survivor_id)
        loser = self.get_race(loser_id)
        if survivor is None or loser is None:
            raise KeyError("both races must exist")
        now = utc_now()
        marked = loser.model_copy(update={"merged_into_id": survivor_id, "merged_at": now, "merge_reason": reason})
        with self._connect() as connection:
            # Source IDs are globally unique, but two manually-created races
            # can still carry the same source/external key.  Keep both audit
            # rows and detach only the duplicate's lookup key before moving
            # it; the surviving keyed row remains the canonical lookup.
            duplicate_sources = connection.execute(
                """
                SELECT loser.id, loser.payload
                FROM race_sources AS loser
                JOIN race_sources AS survivor
                  ON survivor.race_id = ?
                 AND loser.source_name = survivor.source_name
                 AND loser.external_id = survivor.external_id
                WHERE loser.race_id = ? AND loser.external_id IS NOT NULL
                """,
                (str(survivor_id), str(loser_id)),
            ).fetchall()
            for row in duplicate_sources:
                payload = json.loads(row["payload"])
                payload["external_id"] = None
                connection.execute(
                    "UPDATE race_sources SET payload = ?, external_id = NULL WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), row["id"]),
                )
            for table in ("race_sources", "race_aliases", "source_discrepancies", "race_changes"):
                rows = connection.execute(
                    f"SELECT id, payload FROM {table} WHERE race_id = ?", (str(loser_id),)
                ).fetchall()
                for row in rows:
                    payload = json.loads(row["payload"])
                    payload["race_id"] = str(survivor_id)
                    connection.execute(
                        f"UPDATE {table} SET race_id = ?, payload = ? WHERE id = ?",
                        (str(survivor_id), json.dumps(payload, ensure_ascii=False), row["id"]),
                    )
            override_rows = connection.execute(
                "SELECT id, field_name, payload FROM race_field_overrides WHERE race_id = ? ORDER BY active DESC, id",
                (str(loser_id),),
            ).fetchall()
            survivor_fields = {
                row["field_name"]
                for row in connection.execute(
                    "SELECT field_name FROM race_field_overrides WHERE race_id = ? AND active = 1",
                    (str(survivor_id),),
                ).fetchall()
            }
            for row in override_rows:
                if row["field_name"] in survivor_fields and json.loads(row["payload"]).get("active"):
                    payload = json.loads(row["payload"])
                    payload["active"] = False
                    payload["race_id"] = str(survivor_id)
                    connection.execute(
                        "UPDATE race_field_overrides SET race_id = ?, payload = ?, active = 0 WHERE id = ?",
                        (str(survivor_id), json.dumps(payload, ensure_ascii=False), row["id"]),
                    )
                else:
                    payload = json.loads(row["payload"])
                    payload["race_id"] = str(survivor_id)
                    connection.execute(
                        "UPDATE race_field_overrides SET race_id = ?, payload = ? WHERE id = ?",
                        (str(survivor_id), json.dumps(payload, ensure_ascii=False), row["id"]),
                    )
            # Keep open reconciliation references valid after a merge.  The
            # issue stays open for explicit human disposition; only its
            # candidate UUID is rewritten.
            issue_rows = connection.execute(
                "SELECT id, payload FROM reconciliation_issues"
            ).fetchall()
            for row in issue_rows:
                payload = json.loads(row["payload"])
                candidate_ids = payload.get("candidate_race_ids") or []
                if str(loser_id) not in candidate_ids:
                    continue
                rewritten = []
                for candidate_id in candidate_ids:
                    replacement = str(survivor_id) if candidate_id == str(loser_id) else candidate_id
                    if replacement not in rewritten:
                        rewritten.append(replacement)
                payload["candidate_race_ids"] = rewritten
                connection.execute(
                    "UPDATE reconciliation_issues SET payload = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), row["id"]),
                )
            self._save_race(connection, marked)
        return survivor

    def select_merge_survivor(self, race_ids: Iterable[UUID]) -> UUID:
        """Choose a stable survivor for an explicitly approved merge."""

        races = [self.get_race(race_id) for race_id in race_ids]
        races = [race for race in races if race is not None]
        if not races:
            raise KeyError("no races to merge")
        def rank(race: Race) -> tuple[int, int, int, str, str]:
            sources = self.list_sources(race.id)
            current = highest_current_source(sources)
            role = ROLE_RANK[current.source_role] if current else -1
            return (
                int(bool(self.active_overrides(race.id))),
                int(current is not None),
                role,
                # Earlier records win ties; UUID keeps the result deterministic.
                f"{race.created_at.isoformat()}",
                str(race.id),
            )
        return min(races, key=lambda race: (-rank(race)[0], -rank(race)[1], -rank(race)[2], rank(race)[3], rank(race)[4])).id

    def merge_duplicate_races(self, race_ids: Iterable[UUID], *, reason: str) -> Race:
        ids = list(race_ids)
        survivor_id = self.select_merge_survivor(ids)
        survivor = self.get_race(survivor_id)
        assert survivor is not None
        for loser_id in ids:
            if loser_id != survivor_id:
                self.merge_races(survivor_id, loser_id, reason=reason)
        return survivor

    def mark_missing_for_year(
        self,
        source_name: str,
        year: int | None,
        seen_external_ids: set[str],
        *,
        threshold: int = 3,
    ) -> list[RaceSource]:
        """Increment missing counters only after a complete successful list fetch."""

        changed: list[RaceSource] = []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM race_sources WHERE source_name = ?", (source_name,)
            ).fetchall()
            for row in rows:
                source = RaceSource.model_validate(json.loads(row["payload"]))
                if source.source_year != year or not source.external_id or source.external_id in seen_external_ids:
                    continue
                source = source.model_copy(
                    update={
                        "missing_count": source.missing_count + 1,
                        "flag_for_review": source.missing_count + 1 >= threshold,
                    }
                )
                self._save_source(connection, source)
                changed.append(source)
        return changed

    @staticmethod
    def _save_change(connection: sqlite3.Connection, change: RaceChange) -> None:
        connection.execute(
            """
            INSERT INTO race_changes (id, race_id, payload, field_name, source, changed_at, sync_run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(change.id),
                str(change.race_id),
                _dump(change),
                change.field_name,
                change.source,
                change.changed_at.isoformat(),
                str(change.sync_run_id) if change.sync_run_id else None,
            ),
        )

    def list_changes(self, race_id: UUID | None = None) -> list[RaceChange]:
        with self._connect() as connection:
            if race_id:
                rows = connection.execute(
                    "SELECT payload FROM race_changes WHERE race_id = ? ORDER BY changed_at, id",
                    (str(race_id),),
                ).fetchall()
            else:
                rows = connection.execute("SELECT payload FROM race_changes ORDER BY changed_at, id").fetchall()
        return [RaceChange.model_validate(json.loads(row["payload"])) for row in rows]

    def set_field_override(self, race_id: UUID, field_name: str, value: Any, reason: str) -> RaceFieldOverride:
        override = RaceFieldOverride(race_id=race_id, field_name=field_name, override_value=value, reason=reason)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM race_field_overrides WHERE race_id = ? AND field_name = ? AND active = 1",
                (str(race_id), field_name),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                payload["active"] = False
                connection.execute(
                    "UPDATE race_field_overrides SET active = 0, payload = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), row["id"]),
                )
            connection.execute(
                """
                INSERT INTO race_field_overrides (id, race_id, payload, field_name, active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (str(override.id), str(race_id), _dump(override), field_name),
            )
        return override

    def clear_field_override(self, race_id: UUID, field_name: str) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM race_field_overrides WHERE race_id = ? AND field_name = ? AND active = 1",
                (str(race_id), field_name),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                payload["active"] = False
                connection.execute(
                    "UPDATE race_field_overrides SET active = 0, payload = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), row["id"]),
                )

    def active_overrides(self, race_id: UUID) -> dict[str, RaceFieldOverride]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM race_field_overrides WHERE race_id = ? AND active = 1", (str(race_id),)
            ).fetchall()
        overrides = [RaceFieldOverride.model_validate(json.loads(row["payload"])) for row in rows]
        return {override.field_name: override for override in overrides}

    def save_sync_run(self, run: SyncRun) -> SyncRun:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sync_runs (id, payload, source, started_at, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, status=excluded.status
                """,
                (str(run.id), _dump(run), run.source, run.started_at.isoformat(), run.status.value),
            )
        return run

    def finish_sync_run(self, run: SyncRun) -> SyncRun:
        return self.save_sync_run(run)

    def get_sync_run(self, run_id: UUID) -> SyncRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM sync_runs WHERE id = ?", (str(run_id),)).fetchone()
        return SyncRun.model_validate(json.loads(row["payload"])) if row else None

    def list_sync_runs(self, limit: int = 20) -> list[SyncRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM sync_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [SyncRun.model_validate(json.loads(row["payload"])) for row in rows]

    def list_all_sync_runs(self) -> list[SyncRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM sync_runs ORDER BY started_at, id"
            ).fetchall()
        return [SyncRun.model_validate(json.loads(row["payload"])) for row in rows]

    def save_issue(self, issue: ReconciliationIssue) -> ReconciliationIssue:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reconciliation_issues (id, payload, source_name, external_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, status=excluded.status
                """,
                (
                    str(issue.id),
                    _dump(issue),
                    issue.source_name,
                    issue.external_id,
                    issue.status.value,
                    issue.created_at.isoformat(),
                ),
            )
        return issue

    def list_open_issues(self) -> list[ReconciliationIssue]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM reconciliation_issues WHERE status = 'open' ORDER BY created_at, id"
            ).fetchall()
        return [ReconciliationIssue.model_validate(json.loads(row["payload"])) for row in rows]

    def list_issues(self) -> list[ReconciliationIssue]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM reconciliation_issues ORDER BY created_at, id"
            ).fetchall()
        return [ReconciliationIssue.model_validate(json.loads(row["payload"])) for row in rows]

    def resolve_issues_for_source_record(self, source_name: str, external_id: str) -> None:
        now = utc_now()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM reconciliation_issues WHERE source_name = ? AND external_id = ? AND status = 'open'",
                (source_name, external_id),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                payload["status"] = "resolved"
                payload["resolved_at"] = now.isoformat()
                connection.execute(
                    "UPDATE reconciliation_issues SET payload = ?, status = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), "resolved", row["id"]),
                )
