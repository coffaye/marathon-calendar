"""Small, explicit SQLite migrations for the Phase 1 -> Phase 2 schema."""

from __future__ import annotations


MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS races (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            canonical_identity_key TEXT NOT NULL,
            race_date TEXT NOT NULL,
            country TEXT NOT NULL,
            status TEXT NOT NULL,
            sequence INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_races_date ON races(race_date);
        CREATE INDEX IF NOT EXISTS idx_races_country ON races(country);
        CREATE TABLE IF NOT EXISTS race_sources (
            id TEXT PRIMARY KEY,
            race_id TEXT NOT NULL REFERENCES races(id) ON DELETE CASCADE,
            payload TEXT NOT NULL,
            external_id TEXT,
            is_authoritative INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_race_sources_race_id ON race_sources(race_id);
        """,
    ),
    (
        2,
        """
        ALTER TABLE race_sources ADD COLUMN source_name TEXT NOT NULL DEFAULT '';
        ALTER TABLE race_sources ADD COLUMN source_type TEXT NOT NULL DEFAULT '';
        ALTER TABLE race_sources ADD COLUMN source_hash TEXT;
        ALTER TABLE race_sources ADD COLUMN source_year INTEGER;
        ALTER TABLE race_sources ADD COLUMN last_seen_at TEXT;
        ALTER TABLE race_sources ADD COLUMN missing_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE race_sources ADD COLUMN flag_for_review INTEGER NOT NULL DEFAULT 0;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_race_sources_source_external
            ON race_sources(source_name, external_id)
            WHERE external_id IS NOT NULL AND source_name != '';

        CREATE TABLE IF NOT EXISTS race_changes (
            id TEXT PRIMARY KEY,
            race_id TEXT NOT NULL REFERENCES races(id) ON DELETE CASCADE,
            payload TEXT NOT NULL,
            field_name TEXT NOT NULL,
            source TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            sync_run_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_race_changes_race_id ON race_changes(race_id, changed_at);

        CREATE TABLE IF NOT EXISTS race_field_overrides (
            id TEXT PRIMARY KEY,
            race_id TEXT NOT NULL REFERENCES races(id) ON DELETE CASCADE,
            payload TEXT NOT NULL,
            field_name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_override
            ON race_field_overrides(race_id, field_name)
            WHERE active = 1;

        CREATE TABLE IF NOT EXISTS sync_runs (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            source TEXT NOT NULL,
            started_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sync_runs_started_at ON sync_runs(started_at);

        CREATE TABLE IF NOT EXISTS reconciliation_issues (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            source_name TEXT NOT NULL,
            external_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_reconciliation_issues_status
            ON reconciliation_issues(status, created_at);
        """,
    ),
    (
        3,
        """
        ALTER TABLE race_sources ADD COLUMN source_document TEXT;
        ALTER TABLE race_sources ADD COLUMN source_document_checksum TEXT;
        ALTER TABLE race_sources ADD COLUMN source_row_number INTEGER;
        ALTER TABLE race_sources ADD COLUMN source_publication_date TEXT;
        """,
    ),
    (
        4,
        """
        ALTER TABLE races ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'needs_verification';
        ALTER TABLE races ADD COLUMN last_confirmed_at TEXT;
        ALTER TABLE races ADD COLUMN confirmed_by_source_id TEXT;
        ALTER TABLE race_sources ADD COLUMN source_role TEXT NOT NULL DEFAULT 'secondary';
        ALTER TABLE race_sources ADD COLUMN publishable INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE race_sources ADD COLUMN published_at TEXT;
        ALTER TABLE race_sources ADD COLUMN retrieved_at TEXT;
        ALTER TABLE race_sources ADD COLUMN last_confirmed_at TEXT;
        """,
    ),
    (
        5,
        """
        ALTER TABLE races ADD COLUMN merged_into_id TEXT;
        ALTER TABLE races ADD COLUMN merged_at TEXT;
        ALTER TABLE races ADD COLUMN merge_reason TEXT;
        CREATE INDEX IF NOT EXISTS idx_races_merged_into ON races(merged_into_id);

        CREATE TABLE IF NOT EXISTS race_aliases (
            id TEXT PRIMARY KEY,
            race_id TEXT NOT NULL REFERENCES races(id) ON DELETE CASCADE,
            payload TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            source_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_race_aliases_race ON race_aliases(race_id);
        CREATE INDEX IF NOT EXISTS idx_race_aliases_name ON race_aliases(normalized_name);

        CREATE TABLE IF NOT EXISTS unresolved_source_records (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            source_name TEXT NOT NULL,
            external_id TEXT NOT NULL,
            source_year INTEGER,
            last_seen_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_unresolved_source_external
            ON unresolved_source_records(source_name, external_id);

        CREATE TABLE IF NOT EXISTS source_discrepancies (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            race_id TEXT NOT NULL REFERENCES races(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            field_name TEXT NOT NULL,
            detected_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_source_discrepancies_race
            ON source_discrepancies(race_id, detected_at);
        """,
    ),
]


# The application and deployment scripts use this single source of truth when
# deciding whether a database is safe to serve.
CURRENT_SCHEMA_VERSION = max(version for version, _ in MIGRATIONS)
