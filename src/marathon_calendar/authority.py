from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from .domain.models import Race, RaceStatus, RaceSource, SourceRole, VerificationStatus


ROLE_RANK: dict[SourceRole, int] = {
    SourceRole.secondary: 0,
    SourceRole.planning_catalog: 10,
    SourceRole.international_calendar: 15,
    SourceRole.international_federation: 20,
    SourceRole.live_official: 30,
    SourceRole.official_organizer: 40,
}

CURRENT_ROLES = {
    SourceRole.live_official,
    SourceRole.official_organizer,
    SourceRole.international_federation,
    SourceRole.international_calendar,
}
FRESHNESS_WINDOW = timedelta(days=30)

# This registry is source metadata/backward compatibility, not the business
# rule. New adapters should set source_role and publishable explicitly.
LEGACY_SOURCE_ROLE: dict[str, SourceRole] = {
    "china_official": SourceRole.live_official,
    "china_annual_catalog": SourceRole.planning_catalog,
}
LEGACY_PUBLISHABLE: dict[str, bool] = {
    "china_official": True,
    "china_annual_catalog": False,
}


def source_role_for_name(source_name: str) -> SourceRole:
    return LEGACY_SOURCE_ROLE.get(source_name, SourceRole.secondary)


def publishable_default_for_name(source_name: str) -> bool:
    return LEGACY_PUBLISHABLE.get(source_name, False)


def normalize_source_semantics(source: RaceSource) -> RaceSource:
    """Backfill Phase 2 source payloads without changing their provenance."""

    role = source.source_role
    if role is SourceRole.secondary and source.source_name in LEGACY_SOURCE_ROLE:
        role = source_role_for_name(source.source_name)
    publishable = source.publishable
    if not source.publishable and source.source_name in LEGACY_PUBLISHABLE:
        publishable = publishable_default_for_name(source.source_name)
    retrieved_at = source.retrieved_at
    if retrieved_at is None:
        raw_document = source.raw_data.get("document")
        raw_retrieved = raw_document.get("retrieved_at") if isinstance(raw_document, dict) else None
        if raw_retrieved:
            try:
                retrieved_at = datetime.fromisoformat(str(raw_retrieved).replace("Z", "+00:00"))
            except ValueError:
                retrieved_at = None
    retrieved_at = retrieved_at or source.fetched_at
    published_at = source.published_at or source.source_publication_date
    last_confirmed_at = source.last_confirmed_at
    if last_confirmed_at is None and role in CURRENT_ROLES:
        last_confirmed_at = retrieved_at
    return source.model_copy(
        update={
            "source_role": role,
            "publishable": False if role is SourceRole.planning_catalog else publishable,
            "published_at": published_at,
            "retrieved_at": retrieved_at,
            "last_confirmed_at": None if role is SourceRole.planning_catalog else last_confirmed_at,
            "is_authoritative": False if role is SourceRole.planning_catalog else source.is_authoritative,
        }
    )


def _source_time(source: RaceSource) -> datetime | None:
    return source.retrieved_at or source.last_seen_at or source.fetched_at


def freshness_status(source: RaceSource, *, now: datetime | None = None) -> str:
    if source.source_role is SourceRole.planning_catalog:
        return "historical"
    observed = _source_time(source)
    if observed is None:
        return "stale"
    current = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = current - observed.astimezone(timezone.utc)
    return "fresh" if timedelta(0) <= age <= FRESHNESS_WINDOW else "stale"


def is_current_publishable_source(source: RaceSource, *, now: datetime | None = None) -> bool:
    return (
        source.publishable
        and source.source_role is not SourceRole.planning_catalog
        and freshness_status(source, now=now) == "fresh"
    )


def source_priority(source: RaceSource, *, now: datetime | None = None) -> tuple[int, int, float]:
    fresh = 1 if freshness_status(source, now=now) == "fresh" else 0
    observed = _source_time(source)
    timestamp = observed.timestamp() if observed else 0.0
    return ROLE_RANK[source.source_role], fresh, timestamp


def highest_current_source(sources: Iterable[RaceSource], *, now: datetime | None = None) -> RaceSource | None:
    current = [source for source in sources if is_current_publishable_source(source, now=now)]
    return max(current, key=lambda source: source_priority(source, now=now), default=None)


def verification_for_race(
    race: Race, sources: Iterable[RaceSource], *, now: datetime | None = None
) -> VerificationStatus:
    if race.status is RaceStatus.cancelled:
        return VerificationStatus.cancelled
    if highest_current_source(sources, now=now):
        return VerificationStatus.confirmed
    source_list = list(sources)
    if any(source.source_role is SourceRole.planning_catalog for source in source_list):
        return VerificationStatus.planned
    if any(source.source_role in CURRENT_ROLES for source in source_list):
        return VerificationStatus.stale
    return VerificationStatus.needs_verification
