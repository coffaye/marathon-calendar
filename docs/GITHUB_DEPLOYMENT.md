# GitHub-native deployment / GitHub Pages 部署

## Architecture

The production path is:

```text
committed state/ → Actions runner SQLite → China → AIMS → World Athletics
→ global audit → deterministic state export → static site/ → Pages artifact
→ GitHub Pages HTTPS → calendar clients
```

FastAPI is retained for local development, debugging and future API hosting. GitHub Pages never runs Python and never receives the repository root; only the generated `site/` directory is uploaded.

## Workflow

The workflow is [`.github/workflows/update-calendar.yml`](../.github/workflows/update-calendar.yml). It has:

- one daily UTC schedule at `19:17` (not on the hour; GitHub can delay scheduled runs);
- `workflow_dispatch` modes `sync`, `deploy-only`, `test-feed-v1` and `test-feed-v2`;
- `concurrency` with `cancel-in-progress: false`, so two canonical updates cannot run concurrently;
- a 30-minute timeout;
- one ordered sync pipeline: China → AIMS → World Athletics → global audit;
- tests, independent `icalendar` parsing, duplicate/ambiguous gates, state round-trip and static export before artifact upload;
- official `actions/configure-pages`, `actions/upload-pages-artifact` and `actions/deploy-pages` actions;
- `pages: write` and `id-token: write` only on Pages jobs; `contents: write` only on the state-updating job.

There is intentionally no `push` trigger, so the bot's state commit cannot recursively trigger the full sync workflow. The workflow never force-pushes. If a state change appears on the remote branch while a run is executing, the run fails without pushing; an operator reruns after resolving the conflict.

## Transaction and failure ordering

The update job imports the committed state into a temporary DB, performs all source syncs and audit gates, exports a candidate state, round-trips it into a second temporary DB, builds `site/`, and validates the three critical feeds with both the project-independent wire checker and the `icalendar` library. A broken source or gate failure stops before candidate state promotion and before a Pages artifact exists, preserving the previous Pages deployment.

After all gates pass, only a real `state/` diff is committed. The validated site is passed as an Actions artifact to the Pages build/deploy jobs. There is a small unavoidable window where the state commit may succeed and Pages deployment may fail; the prior Pages artifact remains online, and `workflow_dispatch → deploy-only` rebuilds and retries from the now-persisted state. No force push or partial site is used.

## First-time Pages setup

This workspace has no configured `origin` remote or GitHub repository, so Pages cannot be verified here. After the user creates/connects the intended repository and pushes the default branch, the only required UI setting is:

```text
Repository → Settings → Pages → Build and deployment → Source → GitHub Actions
```

No custom domain is required for V1. The workflow derives the project Pages base URL from `GITHUB_REPOSITORY_OWNER` and the repository name; it does not hard-code a username. The expected project URL shape is `https://<owner>.github.io/<repo>/` and the world subscription is `/calendar/world.ics`.

## Content-Type and public surface

After Pages is live, request every feed with `curl -I` and record the actual `Content-Type`, `ETag`, `Last-Modified`, `Cache-Control` and status. Static files do not simulate FastAPI headers; GitHub Pages/CDN owns them. The local exporter validates the content and the workflow checks the artifact, but a public Pages header result cannot be claimed before a repository is connected.

## Scheduled workflow limitation

GitHub documents that scheduled workflows in public repositories can be automatically disabled after 60 days without repository activity. This is a platform reliability risk, not a reason to write fake daily heartbeat commits. Use the documented `workflow_dispatch` fallback and periodically check Actions. Repository visibility and the applicable Pages plan must be checked by the owner; this task does not change private/public visibility.

