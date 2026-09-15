from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
import re
from typing import Any, Callable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .domain.models import (
    Race,
    RaceChange,
    RaceSource,
    ReconciliationIssue,
    SourceRole,
    SyncRun,
    SyncStatus,
    VerificationStatus,
    utc_now,
)
from .authority import ROLE_RANK, freshness_status
from .repository import RaceStore
from .snapshots import SnapshotStore
from .coverage import _province_key
from .identity import normalize_location, normalize_name
from .sources.china_official import (
    DETAIL_ENDPOINT,
    PUBLISHABLE,
    SOURCE_NAME,
    SOURCE_ROLE,
    ChinaOfficialSource,
    NormalizedChinaRecord,
    SourceSchemaError,
    canonical_json_hash,
)
from .sources.http_client import HttpClientError


ANNUAL_SOURCE_NAME = "china_annual_catalog"
_PLANNED_MONTH_RE = re.compile(r"(?P<month>\d{1,2})\s*月")


CALENDAR_VISIBLE_FIELDS = {
    "name",
    "race_date",
    "city",
    "province",
    "status",
    "distance_types",
    "association_level",
    "world_athletics_label",
    "registration_start",
    "registration_end",
    "official_url",
    "organization",
    "start_time",
    "timezone",
}


@dataclass
class SyncOptions:
    year: int | None = None
    page_size: int = 100
    details_limit: int = 0
    detail_delay_seconds: float = 0.1
    missing_threshold: int = 3
    dry_run: bool = False


def _value_for_source(record: NormalizedChinaRecord) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": record.source_race_name,
        "race_date": record.source_race_date,
        "country": record.country,
        "province": record.province,
        "city": record.city,
        "distance_types": record.distance_types,
        "association_level": record.association_level,
        "world_athletics_label": record.world_athletics_label,
        "official_url": record.official_url,
    }
    if record.organization:
        values["organization"] = record.organization
    if record.status is not None:
        values["status"] = record.status
    return values


def _normalized_source_data(record: NormalizedChinaRecord) -> dict[str, Any]:
    return {
        "name": record.source_race_name,
        "race_date": record.source_race_date.isoformat(),
        "province": record.province,
        "city": record.city,
        "distance_types": record.distance_types,
        "source_distance_text": record.source_distance_text,
        "association_level": record.association_level,
        "source_category_text": record.source_category_text,
        "organization": record.organization,
        "world_athletics_label": record.world_athletics_label,
        "status": record.status.value if record.status else None,
        "official_url": record.official_url,
    }


def _source_changes(old: RaceSource, new_data: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    old_data = old.normalized_data
    fields = {
        "name",
        "race_date",
        "province",
        "city",
        "distance_types",
        "source_distance_text",
        "association_level",
        "source_category_text",
        "organization",
        "world_athletics_label",
        "status",
        "official_url",
    }
    return [
        (field, old_data.get(field), new_data.get(field))
        for field in fields
        if old_data.get(field) != new_data.get(field)
    ]


class ChinaSyncService:
    def __init__(
        self,
        store: RaceStore,
        source: ChinaOfficialSource | None = None,
        snapshots: SnapshotStore | None = None,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.store = store
        self.source = source or ChinaOfficialSource()
        self.snapshots = snapshots or SnapshotStore()
        self.sleeper = sleeper

    def _issue_id(self, external_id: str, candidates: list[Race]) -> UUID:
        candidate_part = ",".join(str(race.id) for race in candidates)
        return uuid5(NAMESPACE_URL, f"marathon-calendar:issue:{SOURCE_NAME}:{external_id}:{candidate_part}")

    def _annual_fallback_candidates(self, record: NormalizedChinaRecord) -> list[Race]:
        """Find a prior exact-date catalog Race when the API's city was absent from the catalog."""

        candidates: list[tuple[int, Race]] = []
        api_name = normalize_name(record.source_race_name)
        for source in self.store.list_sources_for_source(ANNUAL_SOURCE_NAME):
            if source.source_year != record.source_race_date.year:
                continue
            normalized = source.normalized_data
            if _province_key(str(normalized.get("province") or "")) != _province_key(record.province):
                continue
            catalog_distances = set(normalized.get("distance_types") or [])
            if not catalog_distances.intersection(record.distance_types):
                continue
            catalog_name = normalize_name(str(normalized.get("name") or source.source_race_name))
            ratio = SequenceMatcher(None, catalog_name, api_name).ratio()
            if catalog_name != api_name and ratio < 0.90:
                continue
            catalog_date = source.source_race_date
            if catalog_date:
                delta = abs((catalog_date - record.source_race_date).days)
                if delta > 120:
                    continue
            else:
                planned = str(normalized.get("planned_date_text") or "")
                months = {int(item.group("month")) for item in _PLANNED_MONTH_RE.finditer(planned)}
                if months and record.source_race_date.month not in months:
                    continue
                delta = 121
            race = self.store.get_race(source.race_id)
            if race:
                candidates.append((delta, race))
        candidates.sort(key=lambda item: (item[0], str(item[1].id)))
        unique: list[Race] = []
        for _, race in candidates:
            if race.id not in {item.id for item in unique}:
                unique.append(race)
        if len(unique) <= 1:
            return unique
        best_delta = candidates[0][0]
        best_ids = {str(race.id) for delta, race in candidates if delta == best_delta}
        return [race for race in unique if str(race.id) in best_ids] if len(best_ids) == 1 else unique

    def _international_fallback_candidates(self, record: NormalizedChinaRecord) -> list[Race]:
        """Match a China API record to an earlier AIMS/WA Race without changing its UID."""

        candidates: list[tuple[int, Race]] = []
        api_name = normalize_name(record.source_race_name)
        api_city = normalize_location(record.city or "")
        for source_name in ("aims", "world_athletics"):
            for source in self.store.list_sources_for_source(source_name):
                if source.source_year != record.source_race_date.year or source.source_race_date is None:
                    continue
                normalized = source.normalized_data
                if normalized.get("country") not in (None, "CHN"):
                    continue
                source_city = normalize_location(str(normalized.get("city") or ""))
                source_name_normalized = normalize_name(str(normalized.get("name") or source.source_race_name))
                city_match = bool(api_city and source_city and api_city == source_city)
                name_match = api_name == source_name_normalized or SequenceMatcher(None, api_name, source_name_normalized).ratio() >= 0.72
                distance_match = bool(set(record.distance_types).intersection(normalized.get("distance_types") or []))
                date_delta = abs((source.source_race_date - record.source_race_date).days)
                if date_delta > 120 or not distance_match or not (city_match or name_match):
                    continue
                score = (50 if city_match else 0) + (40 if api_name == source_name_normalized else 25) + max(0, 20 - date_delta // 7)
                race = self.store.get_race(source.race_id)
                if race:
                    candidates.append((score, race))
        candidates.sort(key=lambda item: (-item[0], str(item[1].id)))
        if not candidates:
            return []
        best_score = candidates[0][0]
        return [race for score, race in candidates if score == best_score]

    def _new_race(self, record: NormalizedChinaRecord) -> Race:
        values = _value_for_source(record)
        values.update(
            {
                "id": uuid4(),
                "year": record.source_race_date.year,
                "timezone": "Asia/Shanghai",
                "race_type": "road_race",
                "canonical_identity_key": record.identity_key,
                "status": record.status or "scheduled",
            }
        )
        return Race.model_validate(values)

    def _source_record(
        self,
        record: NormalizedChinaRecord,
        *,
        race_id: UUID,
        source_id: UUID | None,
        existing: RaceSource | None,
        is_authoritative: bool,
        now,
    ) -> RaceSource:
        preserve_previous_detail = bool(
            existing
            and not record.detail_fetched
            and existing.raw_data.get("_meta", {}).get("detail_fetched")
            and existing.raw_data.get("_meta", {}).get("list_source_hash") == record.list_source_hash
        )
        raw_data = {
            "list": record.list_raw,
            "detail": record.detail_raw if record.detail_fetched else (existing.raw_data.get("detail") if existing else None),
            "_meta": {
                "list_source_hash": record.list_source_hash,
                "detail_fetched": record.detail_fetched or preserve_previous_detail,
                "observed_at": now.isoformat(),
            },
        }
        return RaceSource(
            id=source_id or uuid4(),
            race_id=race_id,
            source_type="official_association",
            source_name=SOURCE_NAME,
            source_url=f"https://www.runchina.org.cn/race/v/detail/{record.external_id}",
            external_id=record.external_id,
            source_race_name=record.source_race_name,
            source_race_date=record.source_race_date,
            raw_data=raw_data,
            source_hash=existing.source_hash if preserve_previous_detail else record.source_hash,
            source_year=record.source_race_date.year,
            normalized_data=existing.normalized_data if preserve_previous_detail else _normalized_source_data(record),
            fetched_at=now,
            verified_at=now if record.detail_fetched else (existing.verified_at if existing else None),
            last_seen_at=now,
            missing_count=0,
            flag_for_review=False,
            source_distance_text=record.source_distance_text,
            confidence=0.98 if record.detail_fetched else 0.9,
            is_authoritative=is_authoritative,
            source_role=SOURCE_ROLE,
            publishable=PUBLISHABLE,
            retrieved_at=now,
            last_confirmed_at=now,
            match_reason=(existing.match_reason if existing else "matched by official external_id or canonical identity"),
        )

    @staticmethod
    def _apply_overrides(values: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        for field_name, override in overrides.items():
            values[field_name] = override.override_value
        return values

    def _effective_race(
        self,
        record: NormalizedChinaRecord,
        current: Race | None,
        overrides: dict[str, Any],
    ) -> Race:
        source_values = _value_for_source(record)
        if current is None:
            values = {
                "id": uuid4(),
                "year": record.source_race_date.year,
                "timezone": "Asia/Shanghai",
                "race_type": "road_race",
                "canonical_identity_key": record.identity_key,
                "status": record.status or "scheduled",
            }
            values.update(source_values)
        else:
            values = current.model_dump(mode="python")
            for field, value in source_values.items():
                if value is not None and (value != [] or field == "distance_types"):
                    values[field] = value
        self._apply_overrides(values, overrides)
        return Race.model_validate(values)

    def _process_record(
        self,
        record: NormalizedChinaRecord,
        run: SyncRun,
        *,
        detail_rows: list[dict[str, Any]],
        detail_budget: list[int],
        detail_delay_seconds: float,
        now,
    ) -> None:
        existing_source = self.store.get_source_by_external(SOURCE_NAME, record.external_id)
        current_race = self.store.get_race(existing_source.race_id) if existing_source else None
        is_new = current_race is None
        if existing_source:
            resolution = "MATCHED"
            run.matched += 1
        else:
            candidates = self.store.find_races_by_identity(record.identity_key)
            if not candidates:
                candidates = self._annual_fallback_candidates(record)
            if not candidates:
                candidates = self._international_fallback_candidates(record)
            if len(candidates) > 1:
                run.ambiguous += 1
                issue = ReconciliationIssue(
                    id=self._issue_id(record.external_id, candidates),
                    source_name=SOURCE_NAME,
                    external_id=record.external_id,
                    candidate_race_ids=[candidate.id for candidate in candidates],
                    reason="multiple canonical races share the identity key; automatic merge refused",
                )
                if not run.dry_run:
                    self.store.save_issue(issue)
                return
            if candidates:
                current_race = candidates[0]
                is_new = False
                resolution = "MATCHED"
                run.matched += 1
            else:
                resolution = "NEW"
                run.new += 1

        needs_detail = (
            existing_source is None
            or not bool(existing_source.raw_data.get("_meta", {}).get("detail_fetched"))
            or existing_source.raw_data.get("_meta", {}).get("list_source_hash") != record.list_source_hash
        )
        if needs_detail and (not detail_budget or detail_budget[0] > 0):
            try:
                detail_raw = self.source.fetch_race_detail(record.external_id)
                record = self.source.normalize_detail(detail_raw, record)
                detail_rows.append({"external_id": record.external_id, "response": detail_raw})
                run.details_fetched += 1
                if detail_budget:
                    detail_budget[0] -= 1
                if self.sleeper and self.source.client is not None and detail_delay_seconds > 0:
                    self.sleeper(detail_delay_seconds)
            except (HttpClientError, SourceSchemaError, ValueError) as exc:
                run.details_failed += 1
                if isinstance(exc, HttpClientError):
                    error = exc.as_dict()
                else:
                    error = {"error_type": type(exc).__name__, "message": str(exc)}
                error.update(
                    {"external_id": record.external_id, "url": f"{DETAIL_ENDPOINT}/{record.external_id}"}
                )
                run.errors.append(error)

        run.normalized += 1
        if not record.city:
            run.missing_city += 1
        if not record.official_url:
            run.missing_official_url += 1
        if record.source_distance_text and not record.distance_types:
            run.unknown_distance += 1
        if record.source_category_text and not record.association_level:
            run.unknown_category += 1

        if current_race is None:
            current_race = self._new_race(record)
        overrides = self.store.active_overrides(current_race.id)
        other_authoritative = [
            source
            for source in self.store.list_sources(current_race.id)
            if source.is_authoritative
            and ROLE_RANK[source.source_role] > ROLE_RANK[SOURCE_ROLE]
            and freshness_status(source, now=now) == "fresh"
            and (not existing_source or source.id != existing_source.id)
        ]
        is_authoritative = (
            existing_source.is_authoritative
            if existing_source and existing_source.source_role is not SourceRole.planning_catalog
            else not other_authoritative
        )
        effective = (
            self._effective_race(record, current_race, overrides)
            if is_authoritative
            else current_race
        )
        if current_race is not None and current_race.id == effective.id:
            meaningful_changed = [
                field
                for field in CALENDAR_VISIBLE_FIELDS
                if getattr(current_race, field, None) != getattr(effective, field, None)
            ]
            source = self._source_record(
                record,
                race_id=effective.id,
                source_id=existing_source.id if existing_source else None,
                existing=existing_source,
                is_authoritative=is_authoritative,
                now=now,
            )
            source_changes = _source_changes(existing_source, source.normalized_data) if existing_source else []
            if is_authoritative and SOURCE_ROLE is not SourceRole.planning_catalog:
                effective = effective.model_copy(
                    update={
                        "verification_status": VerificationStatus.confirmed,
                        "last_confirmed_at": now,
                        "confirmed_by_source_id": source.id,
                    }
                )
            if is_new:
                run.created += 1
            elif not run.dry_run and meaningful_changed:
                modified = now
                if modified <= current_race.last_modified:
                    from datetime import timedelta

                    modified = current_race.last_modified + timedelta(seconds=1)
                effective = effective.model_copy(
                    update={
                        "sequence": current_race.sequence + 1,
                        "last_modified": modified,
                        "updated_at": now,
                        "last_verified_at": now,
                    }
                )
                run.updated += 1
            elif not run.dry_run:
                effective = effective.model_copy(update={"updated_at": now, "last_verified_at": now})
                run.unchanged += 1
            elif meaningful_changed:
                run.updated += 1
            else:
                run.unchanged += 1
            if not run.dry_run:
                changes: list[RaceChange] = []
                for field, old_value, new_value in source_changes:
                    changes.append(
                        RaceChange(
                            race_id=effective.id,
                            field_name=field,
                            old_value=old_value,
                            new_value=new_value,
                            source=SOURCE_NAME,
                            changed_at=now,
                            reason=(
                                "official source changed; manual override preserved effective value"
                                if field in overrides
                                else "official source normalized value changed"
                            ),
                            sync_run_id=run.id,
                            scope="race_source",
                        )
                    )
                for field in meaningful_changed:
                    override = overrides.get(field)
                    changes.append(
                        RaceChange(
                            race_id=effective.id,
                            field_name=field,
                            old_value=getattr(current_race, field, None),
                            new_value=getattr(effective, field, None),
                            source="manual_override" if override else SOURCE_NAME,
                            changed_at=now,
                            reason=override.reason if override else "calendar-visible official source change",
                            sync_run_id=run.id,
                        )
                    )
                self.store.save_source_and_race(effective, source, changes)
    def sync(self, options: SyncOptions | None = None) -> SyncRun:
        options = options or SyncOptions()
        run = SyncRun(source=SOURCE_NAME, year=options.year, dry_run=options.dry_run)
        self.store.save_sync_run(run)
        detail_rows: list[dict[str, Any]] = []
        try:
            result = self.source.fetch_race_list(year=options.year, page_size=options.page_size)
            run.list_records_total = len(result.all_records)
            run.list_records_fetched = len(result.records)
            now = utc_now()
            list_quality_complete = True
            run.snapshot_path = str(
                self.snapshots.save_list(
                    source=SOURCE_NAME,
                    observed_at=now,
                    pages=result.pages,
                    manifest={
                        "source": SOURCE_NAME,
                        "observed_at": now.isoformat(),
                        "request": result.request_payload,
                        "page_count": result.page_count,
                        "total_count": result.total_count,
                        "in_scope_year": options.year,
                    },
                )
            )
            detail_budget = [options.details_limit] if options.details_limit > 0 else []
            for raw in result.records:
                try:
                    record = self.source.normalize_list_item(raw)
                except (SourceSchemaError, ValueError) as exc:
                    list_quality_complete = False
                    run.invalid_date += 1 if "date" in str(exc).lower() else 0
                    run.errors.append(
                        {
                            "external_id": str(raw.get("raceId", "")),
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                    )
                    continue
                self._process_record(
                    record,
                    run,
                    detail_rows=detail_rows,
                    detail_budget=detail_budget,
                    detail_delay_seconds=options.detail_delay_seconds,
                    now=now,
                )
            self.snapshots.save_details(source=SOURCE_NAME, observed_at=now, rows=detail_rows)
            self.snapshots.prune(source=SOURCE_NAME, today=now.date())

            source_records = self.store.list_sources_for_source(SOURCE_NAME)
            seen_ids = set()
            for raw in result.records:
                external_id = str(raw.get("raceId", "")).strip()
                if external_id:
                    seen_ids.add(external_id)
            if not list_quality_complete:
                run.missing = 0
            elif options.dry_run:
                run.missing = sum(
                    1
                    for source_record in source_records
                    if (options.year is None or source_record.source_year == options.year)
                    and source_record.external_id not in seen_ids
                )
            else:
                run.missing = len(
                    self.store.mark_missing_for_year(
                        SOURCE_NAME,
                        options.year,
                        seen_ids,
                        threshold=options.missing_threshold,
                    )
                )
            if run.details_failed or run.errors:
                run.status = SyncStatus.partial_success
            else:
                run.status = SyncStatus.success
        except (HttpClientError, SourceSchemaError, ValueError) as exc:
            run.status = SyncStatus.failed
            if isinstance(exc, HttpClientError):
                run.errors.append(exc.as_dict())
            else:
                run.errors.append({"error_type": type(exc).__name__, "message": str(exc)})
        finally:
            run.finished_at = utc_now()
            self.store.finish_sync_run(run)
        return run
