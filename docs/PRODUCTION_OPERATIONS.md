# Production operations / GitHub 生产运维

## Routine checks

- Check the latest Update Marathon Calendar run and its `GITHUB_STEP_SUMMARY` for China, AIMS, World Athletics, world event count, duplicate UID and audit status.
- Check the `github-pages` deployment environment and the live project URL.
- Request `/calendar/world.ics`, `/calendar/china.ics` and `/calendar/international.ics` over HTTPS and record status and actual headers.
- Periodically run the manual `deploy-only` mode after state-only maintenance.

The Pages artifact contains only `site/`: `index.html`, the public ICS feeds, `data/status.json`, `robots.txt`, `.nojekyll` and the simple 404 page. It does not contain SQLite, `state/`, raw snapshots, source PDFs, reports, `.env` or secrets. `robots.txt` disallows `/calendar/` and `/data/`; that is indexing guidance, not an access-control boundary.

## Data and source failures

The update job is fail-closed. A network error, source schema failure, failed test, state round-trip failure, hard duplicate or invalid ICS stops the workflow before state commit and Pages artifact upload. It does not turn a failed source into an empty feed. Existing missing-record protection and the prior successful Pages deployment remain in effect.

If a run is `partial_success` or `failed`, inspect the workflow summary and temporary diagnostic artifacts from that run. Re-run with `deploy-only` only when the committed state itself is known-good; otherwise fix the source/reconciliation issue and run `sync` again. Annual catalog is a planning source and is not downloaded daily.

## State and manual changes

The canonical identity source is the committed `state/` JSON, not a runner DB. Manual changes must be made through the existing controlled local CLI, exported to state, reviewed as a diff, and pushed normally. Do not edit or commit a production SQLite DB. For a catastrophic bad state publication, follow [STATE_MANAGEMENT.md](STATE_MANAGEMENT.md) and revert the state commit; use normal reconciliation for ordinary corrections.

## Security and supply chain

- No public write/sync/override/delete/migration endpoint is needed; FastAPI remains a local/debug surface.
- Workflow actions are GitHub-maintained official actions with explicit major versions.
- Permissions are job-scoped and minimal: state update gets `contents: write`; Pages jobs get `pages: write` and `id-token: write`.
- No credentials are required for the currently configured public source adapters. Future credentials belong in GitHub Actions Secrets and must never enter state, site or logs.
- The workflow uses concurrency, a timeout and no force push. Human state conflicts fail cleanly.

## Platform risks

GitHub scheduled workflows can be delayed and, for public repositories without activity, can be disabled after 60 days. The repository owner must periodically check Actions and use `workflow_dispatch` when needed. GitHub Pages/CDN caching and calendar-client refresh intervals are outside application control; test with a query-string cache-buster only for manual inspection, never in the stable subscription URL.

