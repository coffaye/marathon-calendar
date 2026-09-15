from __future__ import annotations

import os
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .authority import verification_for_race
from .domain.models import RaceStatus
from .ics import races_to_ics
from .repository import RaceStore
from .publication import races_for_feed
from .realtime import is_formal_calendar_race, is_planned_calendar_race
from .seed import seed_demo
from .migrations import CURRENT_SCHEMA_VERSION
from .test_feed import test_subscription_race


def default_db_path() -> Path:
    return Path(os.environ.get("MARATHON_CALENDAR_DB", "data/marathon_calendar.db"))


def runtime_environment() -> str:
    return os.environ.get("MARATHON_CALENDAR_ENV", "development").strip().casefold() or "development"


def _test_feed_enabled() -> bool:
    return runtime_environment() != "production" and os.environ.get("MARATHON_CALENDAR_TEST_FEED") == "1"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    environment = runtime_environment()
    app = FastAPI(title="Marathon Calendar", version="0.1.0")
    store = RaceStore(db_path or default_db_path())
    if environment != "production":
        seed_demo(store)
    app.state.store = store
    app.state.environment = environment
    app.state.test_feed_enabled = _test_feed_enabled()

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        try:
            payload = _health_payload(store, environment)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return payload

    @app.get("/livez")
    def livez() -> dict[str, str]:
        return {"status": "ok", "service": "marathon-calendar"}

    @app.get("/readyz")
    def readyz() -> dict[str, object]:
        try:
            payload = _health_payload(store, environment)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        if payload["schema"] != CURRENT_SCHEMA_VERSION:
            return JSONResponse(status_code=503, content={**payload, "status": "not_ready"})
        return {**payload, "status": "ready"}

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing_page() -> str:
        return _landing_page(environment)

    @app.get("/robots.txt", include_in_schema=False)
    def robots() -> Response:
        return Response(
            content="User-agent: *\nDisallow: /calendar/\n",
            media_type="text/plain",
        )

    @app.get("/races")
    def races(
        year: int | None = Query(default=None, ge=1900, le=2200),
        country: str | None = None,
        province: str | None = None,
        distance: str | None = None,
        status: RaceStatus | None = None,
        verification: str | None = Query(default=None, pattern="^(confirmed|planned|stale|needs_verification|cancelled)$"),
    ) -> list[dict[str, object]]:
        normalized_country = {"CN": "CHN", "CHN": "CHN"}.get((country or "").upper(), country)
        result = store.list_races(country=normalized_country)
        if year is not None:
            result = [race for race in result if race.year == year]
        if province:
            result = [race for race in result if race.province == province]
        if distance:
            distance_key = distance.casefold()
            aliases = {
                "marathon": {"marathon", "全程", "full marathon"},
                "全程": {"marathon", "全程", "full marathon"},
                "full_marathon": {"marathon", "全程", "full marathon"},
                "half_marathon": {"half_marathon", "half marathon", "半程"},
                "半程": {"half_marathon", "half marathon", "半程"},
                "10k": {"10k", "10公里"},
                "10公里": {"10k", "10公里"},
            }
            accepted = aliases.get(distance_key, {distance_key})
            result = [race for race in result if any(item.casefold() in accepted for item in race.distance_types)]
        if status:
            result = [race for race in result if race.status is status]
        if verification:
            if verification == "confirmed":
                result = [race for race in result if is_formal_calendar_race(race, store.list_sources(race.id))]
            elif verification == "planned":
                result = [race for race in result if is_planned_calendar_race(race, store.list_sources(race.id))]
            else:
                result = [
                    race
                    for race in result
                    if verification_for_race(race, store.list_sources(race.id)).value == verification
                ]
        return [race.model_dump(mode="json") for race in result]

    @app.get("/races/{race_id}/changes")
    def race_changes(race_id: str) -> list[dict[str, object]]:
        try:
            from uuid import UUID

            parsed_id = UUID(race_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid race id") from exc
        if store.get_race(parsed_id) is None:
            raise HTTPException(status_code=404, detail="race not found")
        return [change.model_dump(mode="json") for change in store.list_changes(parsed_id)]

    @app.get("/races/{race_id}/sources")
    def race_sources(race_id: str) -> list[dict[str, object]]:
        try:
            from uuid import UUID

            parsed_id = UUID(race_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid race id") from exc
        if store.get_race(parsed_id) is None:
            raise HTTPException(status_code=404, detail="race not found")
        return [source.model_dump(mode="json") for source in store.list_sources(parsed_id)]

    @app.get("/sync-runs")
    def sync_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, object]]:
        return [run.model_dump(mode="json") for run in store.list_sync_runs(limit)]

    @app.get("/sync-runs/{run_id}")
    def sync_run(run_id: str) -> dict[str, object]:
        try:
            from uuid import UUID

            parsed_id = UUID(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid sync run id") from exc
        run = store.get_sync_run(parsed_id)
        if run is None:
            raise HTTPException(status_code=404, detail="sync run not found")
        return run.model_dump(mode="json")

    @app.get("/calendar/all.ics")
    def calendar_all(request: Request) -> Response:
        return _calendar_response(store.list_races(), "Marathon Calendar", request)

    @app.get("/calendar/china.ics")
    def calendar_china(request: Request) -> Response:
        races = races_for_feed(store, "china")
        return _calendar_response(races, "中国马拉松赛事日历", request)

    @app.get("/calendar/china-planned.ics")
    def calendar_china_planned(request: Request) -> Response:
        races = races_for_feed(store, "china-planned")
        return _calendar_response(
            races,
            "中国马拉松年度规划（含未确认赛事）",
            request,
            planned=True,
        )

    @app.get("/calendar/full-marathon.ics")
    def calendar_full(request: Request) -> Response:
        return _calendar_response(
            races_for_feed(store, "full-marathon"), "Marathon Calendar — Full Marathon", request
        )

    @app.get("/calendar/world.ics")
    def calendar_world(request: Request) -> Response:
        races = races_for_feed(store, "world")
        return _calendar_response(races, "全球马拉松赛事日历", request)

    @app.get("/calendar/international.ics")
    def calendar_international(request: Request) -> Response:
        races = races_for_feed(store, "international")
        return _calendar_response(races, "国际马拉松赛事日历", request)

    if app.state.test_feed_enabled:
        @app.get("/calendar/test-subscription.ics", include_in_schema=False)
        def calendar_test_subscription(request: Request) -> Response:
            return _calendar_response(
                [test_subscription_race()],
                "Marathon Calendar Subscription Test",
                request,
            )

    # Explicit HEAD routes make the reverse proxy/client contract testable on
    # every feed, while FastAPI still emits an empty response body for HEAD.
    @app.head("/calendar/all.ics", include_in_schema=False)
    def calendar_all_head(request: Request) -> Response:
        return calendar_all(request)

    @app.head("/calendar/china.ics", include_in_schema=False)
    def calendar_china_head(request: Request) -> Response:
        return calendar_china(request)

    @app.head("/calendar/china-planned.ics", include_in_schema=False)
    def calendar_china_planned_head(request: Request) -> Response:
        return calendar_china_planned(request)

    @app.head("/calendar/full-marathon.ics", include_in_schema=False)
    def calendar_full_head(request: Request) -> Response:
        return calendar_full(request)

    @app.head("/calendar/world.ics", include_in_schema=False)
    def calendar_world_head(request: Request) -> Response:
        return calendar_world(request)

    @app.head("/calendar/international.ics", include_in_schema=False)
    def calendar_international_head(request: Request) -> Response:
        return calendar_international(request)

    return app


def _health_payload(store: RaceStore, environment: str) -> dict[str, object]:
    schema = store.schema_version()
    latest: dict[str, dict[str, object]] = {}
    for run in store.list_sync_runs(limit=100):
        if run.source in latest:
            continue
        latest[run.source] = {
            "status": run.status.value,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
    return {
        "status": "ok" if schema == CURRENT_SCHEMA_VERSION else "degraded",
        "service": "marathon-calendar",
        "environment": environment,
        "database": "ok",
        "schema": schema,
        "schema_current": schema == CURRENT_SCHEMA_VERSION,
        "race_count": store.count(),
        "last_sync": latest,
    }


def _landing_page(environment: str) -> str:
    mode_note = "production data" if environment == "production" else "development mode"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Marathon Calendar</title></head><body>
<main><h1>Marathon Calendar</h1>
<p>Official and cross-source marathon dates, published as read-only iCalendar feeds.</p>
<p>Runtime: {mode_note}. Subscribe to a feed from your calendar client; do not import it as a one-time file.</p>
<ul>
<li><a href="/calendar/china.ics">China — confirmed</a></li>
<li><a href="/calendar/china-planned.ics">China — annual plans</a></li>
<li><a href="/calendar/international.ics">International</a></li>
<li><a href="/calendar/world.ics">World</a></li>
<li><a href="/healthz">Health</a></li>
</ul></main></body></html>"""


def _calendar_response(races, calendar_name: str, request: Request, *, planned: bool = False) -> Response:
    last_modified = max((race.last_modified for race in races), default=datetime(1970, 1, 1, tzinfo=timezone.utc))
    body = races_to_ics(races, calendar_name=calendar_name, now=last_modified, planned=planned)
    etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest() + '"'
    headers = {
        "Content-Disposition": 'inline; filename="marathon-calendar.ics"',
        "Cache-Control": "public, max-age=900",
        "ETag": etag,
        "Last-Modified": last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "Vary": "If-None-Match, If-Modified-Since",
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    if_modified_since = request.headers.get("if-modified-since")
    if if_modified_since:
        try:
            requested_at = parsedate_to_datetime(if_modified_since).astimezone(timezone.utc)
            if requested_at >= last_modified:
                return Response(status_code=304, headers=headers)
        except (TypeError, ValueError, OverflowError):
            pass
    return Response(
        content=body,
        media_type="text/calendar",
        headers=headers,
    )


app = create_app()


def run() -> None:
    from .cli import main

    main()
