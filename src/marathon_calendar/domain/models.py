from __future__ import annotations

from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class RaceStatus(str, Enum):
    scheduled = "scheduled"
    registration_open = "registration_open"
    registration_closed = "registration_closed"
    postponed = "postponed"
    cancelled = "cancelled"
    completed = "completed"
    date_tentative = "date_tentative"


class SyncStatus(str, Enum):
    running = "running"
    success = "success"
    partial_success = "partial_success"
    failed = "failed"


class ReconciliationStatus(str, Enum):
    open = "open"
    resolved = "resolved"
    rejected = "rejected"


class SourceRole(str, Enum):
    live_official = "live_official"
    official_organizer = "official_organizer"
    planning_catalog = "planning_catalog"
    international_calendar = "international_calendar"
    international_federation = "international_federation"
    secondary = "secondary"


class VerificationStatus(str, Enum):
    confirmed = "confirmed"
    planned = "planned"
    stale = "stale"
    needs_verification = "needs_verification"
    cancelled = "cancelled"


class Race(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    name_en: str | None = None
    year: int = Field(ge=1900, le=2200)
    country: str = Field(min_length=2, max_length=3)
    province: str | None = None
    city: str | None = None
    race_date: date
    start_time: time | None = None
    timezone: str | None = "Asia/Shanghai"
    distance_types: list[str] = Field(default_factory=list)
    race_type: str = "road_race"
    organization: str | None = None
    association_level: str | None = None
    world_athletics_label: str | None = None
    status: RaceStatus = RaceStatus.scheduled
    verification_status: VerificationStatus = VerificationStatus.needs_verification
    registration_start: date | None = None
    registration_end: date | None = None
    lottery_result_date: date | None = None
    official_url: HttpUrl | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    canonical_identity_key: str
    sequence: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_verified_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    confirmed_by_source_id: UUID | None = None
    last_modified: datetime = Field(default_factory=utc_now)
    merged_into_id: UUID | None = None
    merged_at: datetime | None = None
    merge_reason: str | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @field_validator("official_url", mode="before")
    @classmethod
    def empty_url_is_missing(cls, value: Any) -> Any:
        return None if value == "" else value


class RaceSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    race_id: UUID
    source_type: str
    source_name: str
    source_url: HttpUrl
    external_id: str | None = None
    source_race_name: str
    source_race_date: date | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)
    source_hash: str | None = None
    source_year: int | None = Field(default=None, ge=1900, le=2200)
    source_document: str | None = None
    source_document_checksum: str | None = None
    source_row_number: int | None = Field(default=None, ge=1)
    source_publication_date: date | None = None
    source_role: SourceRole = SourceRole.secondary
    publishable: bool = False
    published_at: date | None = None
    retrieved_at: datetime | None = None
    last_confirmed_at: datetime | None = None
    normalized_data: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=utc_now)
    verified_at: datetime | None = None
    last_seen_at: datetime | None = None
    missing_count: int = Field(default=0, ge=0)
    flag_for_review: bool = False
    source_distance_text: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    is_authoritative: bool = False
    match_reason: str | None = None


OVERRIDABLE_FIELDS = frozenset({"race_date", "name", "status", "city", "province", "official_url"})


class RaceFieldOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    race_id: UUID
    field_name: str
    override_value: Any
    source: str = "manual"
    reason: str
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("field_name")
    @classmethod
    def validate_field_name(cls, value: str) -> str:
        if value not in OVERRIDABLE_FIELDS:
            raise ValueError(f"Field cannot be overridden: {value}")
        return value


class RaceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    race_id: UUID
    field_name: str
    old_value: Any = None
    new_value: Any = None
    source: str
    changed_at: datetime = Field(default_factory=utc_now)
    reason: str
    sync_run_id: UUID | None = None
    scope: Literal["race", "race_source"] = "race"


class RaceAlias(BaseModel):
    """A source-specific spelling retained for auditable identity matching."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    race_id: UUID
    name: str = Field(min_length=1)
    language: str | None = None
    source_id: UUID | None = None
    normalized_name: str
    created_at: datetime = Field(default_factory=utc_now)


class UnresolvedSourceRecord(BaseModel):
    """A source record that is valid but has no trusted single race date yet."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    source_name: str
    external_id: str
    source_url: HttpUrl
    source_race_name: str
    source_year: int | None = Field(default=None, ge=1900, le=2200)
    date_precision: str
    source_race_date: date | None = None
    source_end_date: date | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)
    normalized_data: dict[str, Any] = Field(default_factory=dict)
    reason: str
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)


class SourceDiscrepancy(BaseModel):
    """A source value that differs from the effective canonical value."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    race_id: UUID
    source_id: UUID
    source_name: str
    field_name: str
    canonical_value: Any = None
    source_value: Any = None
    status: ReconciliationStatus = ReconciliationStatus.open
    detected_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class ReconciliationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    source_record_id: UUID | None = None
    source_name: str
    external_id: str
    candidate_race_ids: list[UUID] = Field(default_factory=list)
    reason: str
    status: ReconciliationStatus = ReconciliationStatus.open
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class SyncRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    source: str
    year: int | None = Field(default=None, ge=1900, le=2200)
    dry_run: bool = False
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    status: SyncStatus = SyncStatus.running
    list_records_total: int = 0
    list_records_fetched: int = 0
    details_fetched: int = 0
    details_failed: int = 0
    normalized: int = 0
    matched: int = 0
    new: int = 0
    ambiguous: int = 0
    invalid_date: int = 0
    missing_city: int = 0
    missing_official_url: int = 0
    unknown_distance: int = 0
    unknown_category: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    snapshot_path: str | None = None
