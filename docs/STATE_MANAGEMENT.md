# Versioned canonical state / 版本化 canonical state

## Why state is not an Actions database

GitHub Actions runners are disposable. A runner-local SQLite file, Actions cache or uploaded artifact can disappear before the next run and is not a safe identity authority. Rebuilding from raw sources alone could create new UUIDs, lose source links, forget manual overrides, or reset `SEQUENCE`; that would make calendar clients treat updates as new events.

The committed `state/` directory is the durable source of canonical identity. SQLite remains the runtime working database used by the existing repository and sync code.

## Files

```text
state/
├── canonical_races.json       Race.id, canonical fields, status, sequence, timestamps
├── source_links.json          RaceSource mappings and source evidence metadata
├── aliases.json               RaceAlias identity history
├── manual_overrides.json      active and historical override values/reasons
├── reconciliation.json        issues, unresolved records, discrepancies
├── history.json               RaceChange audit history
└── metadata.json              format/schema versions, counts and state policy
```

JSON is UTF-8, sorted by stable keys, indented, newline-terminated and deterministic. Raw HTTP payloads and volatile observation timestamps (`updated_at`, `last_verified_at`, `fetched_at`, `retrieved_at`, source `last_confirmed_at`, `verified_at`, and `last_seen_at`) are intentionally not included; raw snapshots may be uploaded as short-lived diagnostic artifacts. Stable source hashes, missing counters, confirmation values and publication metadata remain. Local filesystem paths, tokens, cookies and personal data are excluded.

## Export and import

```powershell
$env:PYTHONPATH = "src"
python -m marathon_calendar state export --db data/current.db --output state
python -m marathon_calendar state import --state state --db tmp/runtime.db
```

Import runs the existing v1-v5 migration system before inserting state. It restores Race IDs, source mappings, aliases, overrides, reconciliation decisions, change history and the persisted calendar `sequence`/`last_modified` values. Transient `SyncRun` rows and volatile observation timestamps are intentionally not committed; the current runner records sync outcomes in `status.json`/workflow summary. Repeated identical source observations do not rewrite discrepancy detection times or append duplicate aliases. The normal workflow imports into a new temporary DB; it never overwrites the prior production DB. `--replace` is available only as an explicit local operator action.

## Recovery and revert

For a clearly catastrophic publication, revert the bad state commit and manually dispatch the workflow in `deploy-only` mode:

```bash
git revert <bad-state-commit>
git push origin <default-branch>
```

Then use GitHub → Actions → Update Marathon Calendar → Run workflow → `deploy-only`. The workflow rebuilds SQLite and the Pages artifact from the reverted state. `git revert` is not a reconciliation or merge tool: if new correct source data exists, resolve it through the normal sync/reconciliation process instead.

Manual overrides must be written through the existing controlled CLI and then exported into `state/manual_overrides.json`; they must not live only in a developer laptop database.
