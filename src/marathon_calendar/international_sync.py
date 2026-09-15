"""Conservative synchronization and reconciliation for international sources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from uuid import NAMESPACE_URL, UUID, uuid5

from .authority import ROLE_RANK, highest_current_source
from .domain.models import (
    Race,
    RaceAlias,
    RaceChange,
    RaceSource,
    RaceStatus,
    ReconciliationIssue,
    SourceDiscrepancy,
    SourceRole,
    SyncRun,
    SyncStatus,
    UnresolvedSourceRecord,
    VerificationStatus,
    utc_now,
)
from .identity import canonical_identity_key, country_to_alpha2, normalize_location, normalize_name
from .repository import RaceStore
from .snapshots import SnapshotStore
from .sources.aims import AimsEvent, AimsFetchResult, SOURCE_NAME as AIMS_NAME, SOURCE_ROLE as AIMS_ROLE
from .sources.world_athletics import WorldAthleticsEvent, WorldAthleticsFetchResult, SOURCE_NAME as WA_NAME, SOURCE_ROLE as WA_ROLE


MAIN_DISTANCE_TYPES = {"marathon", "half_marathon", "全程", "半程", "full marathon", "half marathon"}


@dataclass
class InternationalSyncResult:
    run: SyncRun
    report: dict[str, Any]
    report_paths: list[Path]


def is_main_distance(race: Race) -> bool:
    return any(item.casefold() in MAIN_DISTANCE_TYPES for item in race.distance_types)


def _source_fields(event: AimsEvent | WorldAthleticsEvent) -> dict[str, Any]:
    if isinstance(event, AimsEvent):
        return {
            "name": event.summary,
            "race_date": event.race_date,
            "city": event.city,
            "country": event.country,
            "distance_types": event.distance_types,
            "status": event.status,
            "official_url": event.official_url,
        }
    return {
        "name": event.name,
        "race_date": event.race_date,
        "city": event.city,
        "country": event.country,
        "distance_types": event.distance_types,
        "status": event.status,
        "official_url": event.official_url,
        "world_athletics_label": event.label,
    }


def _serial(value: Any) -> Any:
    if isinstance(value, (date, datetime, UUID)):
        return value.isoformat() if not isinstance(value, UUID) else str(value)
    if isinstance(value, list):
        return [_serial(item) for item in value]
    if isinstance(value, dict):
        return {key: _serial(item) for key, item in value.items()}
    if hasattr(value, "value"):
        return value.value
    return value


def _same_value(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    left_text = str(left)
    right_text = str(right)
    if "://" in left_text or "://" in right_text:
        # Pydantic normalizes HttpUrl roots with a trailing slash while source
        # documents commonly omit it. Treat that representation difference as
        # equal so a replay cannot consume a calendar SEQUENCE.
        return left_text.strip().rstrip("/") == right_text.strip().rstrip("/")
    if isinstance(left, (date, datetime)) or isinstance(right, (date, datetime)):
        return _serial(left) == _serial(right)
    if hasattr(left, "value") or hasattr(right, "value"):
        return _serial(left) == _serial(right)
    if isinstance(left, (list, tuple, set)) or isinstance(right, (list, tuple, set)):
        return list(left) == list(right)
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, str) and not isinstance(right, str):
        return left == str(right)
    if isinstance(right, str) and not isinstance(left, str):
        return str(left) == right
    return left == right


def _validated_race_copy(race: Race, values: dict[str, Any]) -> Race:
    payload = race.model_dump(mode="python")
    payload.update(values)
    return Race.model_validate(payload)


def _language(name: str) -> str:
    return "zh" if any("\u4e00" <= char <= "\u9fff" for char in name) else "en"


def _event_url(event: AimsEvent | WorldAthleticsEvent, calendar_url: str) -> str:
    return (event.official_url or calendar_url).strip()


def _event_identity_name(event: AimsEvent | WorldAthleticsEvent) -> str:
    return event.summary if isinstance(event, AimsEvent) else event.name


def _event_id(event: AimsEvent | WorldAthleticsEvent) -> str:
    return event.uid if isinstance(event, AimsEvent) else str(event.competition_id)


class InternationalSyncService:
    def __init__(
        self,
        store: RaceStore,
        *,
        source_name: str,
        source_role: SourceRole,
        calendar_url: str,
        snapshots: SnapshotStore | None = None,
    ):
        self.store = store
        self.source_name = source_name
        self.source_role = source_role
        self.calendar_url = calendar_url
        self.snapshots = snapshots or SnapshotStore()
        self._race_cache: list[Race] | None = None
        self._alias_cache: dict[UUID, set[str]] = {}
        self._source_names_cache: dict[UUID, set[str]] = {}

    def _issue_id(self, external_id: str, candidates: Iterable[Race]) -> UUID:
        candidate_part = ",".join(sorted(str(race.id) for race in candidates))
        return uuid5(NAMESPACE_URL, f"marathon-calendar:international-issue:{self.source_name}:{external_id}:{candidate_part}")

    def _candidates(self, event: AimsEvent | WorldAthleticsEvent) -> list[Race]:
        source = self.store.get_source_by_external(self.source_name, _event_id(event))
        if source:
            race = self.store.get_race(source.race_id)
            return [race] if race and race.merged_into_id is None else []
        name = _event_identity_name(event)
        country = event.country
        event_date = event.race_date
        city = getattr(event, "city", None)
        normalized_event_name = normalize_name(name)
        normalized_city = normalize_location(city or "")
        key = canonical_identity_key(name=name, country=country or "UNK", city=city)
        candidates: list[tuple[int, Race]] = []
        races = self._race_cache if self._race_cache is not None else self.store.list_races()
        for race in races:
            if race.year != (event_date.year if event_date else race.year):
                continue
            if country and race.country != country:
                continue
            if event_date and event_date != race.race_date and self.source_name in self._source_names_cache.get(race.id, set()):
                # Two different records from the same source with the same
                # normalized title but different dates are not automatically
                # a reschedule; their stable source IDs must remain separate.
                continue
            race_name = normalize_name(race.name)
            alias_names = self._alias_cache.get(race.id, set())
            name_match = normalized_event_name == race_name or normalized_event_name in alias_names or normalized_event_name == normalize_name(race.name_en or "")
            city_match = bool(normalized_city and normalized_city == normalize_location(race.city or ""))
            # This bridges common English/Chinese names (e.g. Xiamen/厦门)
            # without allowing a city/date coincidence to absorb another
            # event in a crowded city.
            name_city_match = bool(city_match and normalized_city and normalized_city in normalized_event_name and normalized_city in race_name)
            name_match = name_match or name_city_match
            distance_match = bool(set(event.distance_types).intersection(race.distance_types)) or not event.distance_types or not race.distance_types
            date_match = bool(event_date and abs((race.race_date - event_date).days) <= 120)
            identity_match = race.canonical_identity_key == key
            if not distance_match:
                continue
            score = 0
            if identity_match:
                score += 100
            if name_match:
                score += 60
            elif city_match and SequenceMatcher(None, normalized_event_name, race_name).ratio() >= 0.90:
                score += 35
            if city_match:
                score += 25
            if date_match:
                date_delta = abs((race.race_date - event_date).days) if event_date else 120
                score += max(0, 60 - min(date_delta, 60)) if (name_match or city_match) else 5
            if score >= 60 and (identity_match or name_match):
                candidates.append((score, race))
        candidates.sort(key=lambda item: (-item[0], str(item[1].id)))
        if not candidates:
            return []
        best_score = candidates[0][0]
        best = [race for score, race in candidates if score == best_score]
        return best if len(best) == 1 else [race for _, race in candidates]

    def _new_race(self, event: AimsEvent | WorldAthleticsEvent, identity: str) -> Race:
        fields = _source_fields(event)
        race_date = fields["race_date"]
        assert race_date is not None
        return Race.model_validate({
            "id": uuid5(NAMESPACE_URL, f"marathon-calendar:race:{self.source_name}:{_event_id(event)}"),
            "name": fields["name"],
            "name_en": fields["name"],
            "year": race_date.year,
            "country": fields["country"],
            "city": fields["city"],
            "race_date": race_date,
            "timezone": None,
            "distance_types": fields["distance_types"],
            "race_type": "road_race",
            "world_athletics_label": fields.get("world_athletics_label"),
            "status": fields["status"] or RaceStatus.scheduled,
            "verification_status": VerificationStatus.confirmed,
            "canonical_identity_key": identity,
        })

    def _normalized_data(self, event: AimsEvent | WorldAthleticsEvent) -> dict[str, Any]:
        fields = _source_fields(event)
        fields.update({
            "external_id": _event_id(event),
            "date_precision": event.date_precision,
            "end_date": getattr(event, "end_date", None),
            "country_raw": getattr(event, "country_raw", None),
            "source_status": getattr(event, "source_status", None),
            "label": getattr(event, "label", None),
            "ranking_category": getattr(event, "ranking_category", None),
            "sequence": getattr(event, "sequence", None),
            "last_modified": getattr(event, "last_modified", None),
            "location": getattr(event, "location", None),
        })
        return _serial(fields)

    def _source_record(
        self,
        event: AimsEvent | WorldAthleticsEvent,
        *,
        race_id: UUID,
        source_id: UUID | None,
        authoritative: bool,
        fetched_at: datetime,
        document_checksum: str,
    ) -> RaceSource:
        source_hash = hashlib.sha256(json.dumps(event.raw_data, ensure_ascii=False, sort_keys=True, default=_serial).encode("utf-8")).hexdigest()
        return RaceSource(
            id=source_id or uuid5(NAMESPACE_URL, f"marathon-calendar:source:{self.source_name}:{_event_id(event)}"),
            race_id=race_id,
            source_type="international_calendar" if self.source_name == AIMS_NAME else "world_athletics_label_road_race",
            source_name=self.source_name,
            source_url=_event_url(event, self.calendar_url),
            external_id=_event_id(event),
            source_race_name=_event_identity_name(event),
            source_race_date=event.race_date,
            raw_data={"event": event.raw_data, "calendar_url": self.calendar_url},
            source_hash=source_hash,
            source_year=event.race_date.year if event.race_date else (event.end_date.year if event.end_date else None),
            source_document=self.calendar_url,
            source_document_checksum=document_checksum,
            source_role=self.source_role,
            publishable=True,
            retrieved_at=fetched_at,
            last_confirmed_at=fetched_at,
            normalized_data=self._normalized_data(event),
            fetched_at=fetched_at,
            verified_at=fetched_at,
            last_seen_at=fetched_at,
            source_distance_text=list(event.distance_types),
            confidence=0.92 if isinstance(event, WorldAthleticsEvent) else 0.86,
            is_authoritative=authoritative,
            match_reason="stable source identifier or conservative country/name/date reconciliation",
        )

    def _save_discrepancies(self, race: Race, source: RaceSource, before: Race | None, event: AimsEvent | WorldAthleticsEvent, detected_at: datetime) -> None:
        if before is None:
            return
        fields = _source_fields(event)
        values = {
            "name": (before.name, fields.get("name")),
            "race_date": (before.race_date, fields.get("race_date")),
            "city": (before.city, fields.get("city")),
            "country": (before.country, fields.get("country")),
            "distance_types": (sorted(before.distance_types), sorted(fields.get("distance_types") or [])),
            "status": (before.status, fields.get("status")),
        }
        for field, (canonical, source_value) in values.items():
            if source_value in (None, [], "") or canonical == source_value:
                continue
            discrepancy_id = uuid5(NAMESPACE_URL, f"marathon-calendar:discrepancy:{race.id}:{source.id}:{field}:{_serial(source_value)}")
            self.store.save_discrepancy(SourceDiscrepancy(
                id=discrepancy_id,
                race_id=race.id,
                source_id=source.id,
                source_name=source.source_name,
                field_name=field,
                canonical_value=_serial(canonical),
                source_value=_serial(source_value),
                detected_at=detected_at,
            ))

    def _unresolved(self, event: AimsEvent | WorldAthleticsEvent, fetched_at: datetime, reason: str) -> UnresolvedSourceRecord:
        return UnresolvedSourceRecord(
            id=uuid5(NAMESPACE_URL, f"marathon-calendar:unresolved:{self.source_name}:{_event_id(event)}"),
            source_name=self.source_name,
            external_id=_event_id(event),
            source_url=_event_url(event, self.calendar_url),
            source_race_name=_event_identity_name(event),
            source_year=event.race_date.year if event.race_date else (event.end_date.year if event.end_date else None),
            date_precision=event.date_precision,
            source_race_date=event.race_date,
            source_end_date=event.end_date,
            raw_data=event.raw_data,
            normalized_data=self._normalized_data(event),
            reason=reason,
            first_seen_at=fetched_at,
            last_seen_at=fetched_at,
        )

    def sync(
        self,
        fetch_result: AimsFetchResult | WorldAthleticsFetchResult,
        *,
        year: int | None = None,
        dry_run: bool = False,
        report_dir: str | Path = "reports",
    ) -> InternationalSyncResult:
        fetched_at = fetch_result.fetched_at
        events = fetch_result.events
        self._race_cache = self.store.list_races()
        self._alias_cache = {}
        for alias in self.store.list_aliases():
            self._alias_cache.setdefault(alias.race_id, set()).add(alias.normalized_name)
        self._source_names_cache = {}
        for source in self.store.list_sources():
            self._source_names_cache.setdefault(source.race_id, set()).add(source.source_name)
        run = SyncRun(source=self.source_name, year=year, dry_run=dry_run, started_at=fetched_at)
        run.list_records_total = len(events)
        run.list_records_fetched = len(events)
        run.snapshot_path = str(self.snapshots.save_text(
            source=self.source_name,
            observed_at=fetched_at,
            filename="events.ics" if self.source_name == AIMS_NAME else "page.html",
            content=fetch_result.raw_text if self.source_name == AIMS_NAME else fetch_result.raw_html,
            manifest={"source_url": fetch_result.source_url, "document_checksum": fetch_result.document_checksum, "records": len(events)},
        ))
        if self.source_name == WA_NAME:
            self.snapshots.save_json(source=self.source_name, observed_at=fetched_at, filename="evidence.json", value=fetch_result.evidence)
        ambiguous: list[str] = []
        unresolved = 0
        relevant = 0
        source_only = 0
        cross_source = 0
        for event in events:
            if year is not None and event.race_date and event.race_date.year != year:
                continue
            run.normalized += 1
            if not event.country:
                unresolved += 1
                run.unknown_category += 1
                if not dry_run:
                    record = self._unresolved(event, fetched_at, "country code/name is unknown; no country guessed")
                    if record.source_year is None and year is not None:
                        record = record.model_copy(update={"source_year": year})
                    self.store.save_unresolved_source_record(record)
                continue
            if event.date_precision != "exact" or event.race_date is None:
                unresolved += 1
                run.invalid_date += 1
                if not dry_run:
                    record = self._unresolved(event, fetched_at, "date is TBC or a multi-day range; no race day inferred")
                    if record.source_year is None and year is not None:
                        record = record.model_copy(update={"source_year": year})
                    self.store.save_unresolved_source_record(record)
                continue
            candidates = self._candidates(event)
            if len(candidates) > 1:
                ambiguous.append(_event_id(event))
                run.ambiguous += 1
                if not dry_run:
                    self.store.save_issue(ReconciliationIssue(
                        id=self._issue_id(_event_id(event), candidates),
                        source_name=self.source_name,
                        external_id=_event_id(event),
                        candidate_race_ids=[race.id for race in candidates],
                        reason="multiple conservative cross-source candidates; no automatic merge",
                    ))
                continue
            current = candidates[0] if candidates else None
            if current is None:
                identity = canonical_identity_key(name=_event_identity_name(event), country=event.country, city=getattr(event, "city", None))
                race = self._new_race(event, identity)
                if self._race_cache is not None:
                    self._race_cache.append(race)
                self._source_names_cache.setdefault(race.id, set())
                source_only += 1
                before = None
            else:
                race = current
                before = current
                existing_sources = self.store.list_sources(current.id)
                higher = highest_current_source(existing_sources)
                authoritative = higher is None or ROLE_RANK[self.source_role] >= ROLE_RANK[higher.source_role]
                same_source_records = [item for item in existing_sources if item.source_name == self.source_name]
                # A festival may expose multiple same-source rows (marathon,
                # half, 10K).  Once a Race already has more than the current
                # row from this source, do not let row order churn the
                # canonical display fields on every replay.  The raw rows and
                # their distances remain fully preserved.
                allow_effective_update = authoritative and len(same_source_records) <= 1
                if allow_effective_update:
                    values = _source_fields(event)
                    overrides = self.store.active_overrides(current.id)
                    for field, value in list(values.items()):
                        if value in (None, [], "") or field in overrides:
                            values.pop(field, None)
                    # A source observation without an explicit status must not
                    # revive a race already marked cancelled. An explicit
                    # rescheduled/scheduled status is allowed to confirm it.
                    if not (
                        current.status is RaceStatus.cancelled
                        and values.get("status") in (None, RaceStatus.cancelled)
                    ):
                        values["verification_status"] = VerificationStatus.confirmed
                    if isinstance(event, WorldAthleticsEvent) and event.label:
                        values["world_athletics_label"] = event.label
                    changed = any(not _same_value(getattr(current, field), value) for field, value in values.items() if hasattr(current, field))
                    if changed:
                        now = fetched_at
                        if now <= current.last_modified:
                            now = current.last_modified + timedelta(seconds=1)
                        values.update({"sequence": current.sequence + 1, "last_modified": now, "updated_at": now})
                        race = _validated_race_copy(current, values)
                else:
                    race = current.model_copy(update={"verification_status": current.verification_status})
                cross_source += 1
            if any(item in MAIN_DISTANCE_TYPES for item in event.distance_types):
                relevant += 1
            source_id = self.store.get_source_by_external(self.source_name, _event_id(event))
            existing_sources = self.store.list_sources(race.id) if current else []
            higher = highest_current_source(existing_sources)
            authoritative = higher is None or ROLE_RANK[self.source_role] >= ROLE_RANK[higher.source_role]
            source = self._source_record(
                event,
                race_id=race.id,
                source_id=source_id.id if source_id else None,
                authoritative=authoritative,
                fetched_at=fetched_at,
                document_checksum=fetch_result.document_checksum,
            )
            changes: list[RaceChange] = []
            if before is not None and race != before:
                for field in ("name", "race_date", "city", "country", "distance_types", "status", "world_athletics_label"):
                    old = getattr(before, field, None)
                    new = getattr(race, field, None)
                    if old != new:
                        changes.append(RaceChange(race_id=race.id, field_name=field, old_value=_serial(old), new_value=_serial(new), source=self.source_name, changed_at=race.last_modified, reason="international source authority update"))
            if not dry_run:
                self.store.save_source_and_race(race, source, changes)
                self.store.add_alias(RaceAlias(race_id=race.id, name=_event_identity_name(event), language=_language(_event_identity_name(event)), source_id=source.id, normalized_name=normalize_name(_event_identity_name(event))))
                self._alias_cache.setdefault(race.id, set()).add(normalize_name(_event_identity_name(event)))
                self._source_names_cache.setdefault(race.id, set()).add(self.source_name)
                self._save_discrepancies(race, source, before, event, fetched_at)
                self.store.resolve_unresolved_source_record(self.source_name, _event_id(event))
                self.store.resolve_issues_for_source_record(self.source_name, _event_id(event))
            run.matched += int(current is not None)
            run.new += int(current is None)
            run.updated += int(bool(changes))
            run.unchanged += int(current is not None and not changes)
        run.details_fetched = len(events)
        run.status = SyncStatus.partial_success if ambiguous or unresolved else SyncStatus.success
        run.finished_at = utc_now()
        report = build_international_report(self.store, run, events, unresolved=unresolved, relevant=relevant, ambiguous=ambiguous, source_only=source_only, cross_source=cross_source, fetch_result=fetch_result)
        report_paths = write_international_report(report, report_dir)
        # A dry-run is still an auditable synchronization attempt.  It may
        # write the SyncRun and raw snapshot, but never canonical data.
        self.store.save_sync_run(run)
        return InternationalSyncResult(run=run, report=report, report_paths=report_paths)


def build_international_report(store: RaceStore, run: SyncRun, events: list[Any], *, unresolved: int, relevant: int, ambiguous: list[str], source_only: int, cross_source: int, fetch_result: Any) -> dict[str, Any]:
    sources = store.list_sources_for_source(run.source)
    return {
        "source": run.source,
        "year": run.year,
        "retrieved_at": fetch_result.fetched_at.isoformat(),
        "source_url": fetch_result.source_url,
        "document_checksum": fetch_result.document_checksum,
        "records": {"fetched": len(events), "exact_date": sum(e.date_precision == "exact" for e in events), "range_or_tbc": sum(e.date_precision != "exact" for e in events), "relevant_marathon_or_half": relevant, "unresolved": unresolved},
        "persistence": {"source_records": len(sources), "new_races": source_only, "matched_races": cross_source, "ambiguous": len(ambiguous)},
        "ambiguous_external_ids": ambiguous,
        "current_publishable_source_records": sum(item.publishable for item in sources),
        "status": run.status.value,
    }


def write_international_report(report: dict[str, Any], report_dir: str | Path) -> list[Path]:
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    year = report.get("year") or "all"
    source = report["source"]
    json_path = directory / f"{source}_coverage_{year}.json"
    md_path = directory / f"{source}_coverage_{year}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_serial) + "\n", encoding="utf-8")
    records = report["records"]
    md_path.write_text("\n".join([
        f"# {source} coverage — {year}", "", f"- Source: {report['source_url']}", f"- Retrieved: {report['retrieved_at']}", f"- Document SHA-256: `{report['document_checksum']}`", "", "| Metric | Count |", "|---|---:|", f"| Fetched | {records['fetched']} |", f"| Exact-date | {records['exact_date']} |", f"| Range/TBC | {records['range_or_tbc']} |", f"| Marathon/half-marathon | {records['relevant_marathon_or_half']} |", f"| Unresolved | {records['unresolved']} |", f"| Ambiguous | {report['persistence']['ambiguous']} |", "",
    ]) + "\n", encoding="utf-8")
    return [json_path, md_path]


def build_international_coverage_report(store: RaceStore, *, year: int) -> dict[str, Any]:
    """Build the requested combined AIMS/WA quality report from persisted evidence."""

    latest_runs: dict[str, SyncRun] = {}
    run_history: dict[str, list[SyncRun]] = {AIMS_NAME: [], WA_NAME: []}
    for run in store.list_sync_runs(500):
        if run.source in {AIMS_NAME, WA_NAME} and run.year == year and run.source not in latest_runs:
            latest_runs[run.source] = run
        if run.source in run_history and run.year == year:
            run_history[run.source].append(run)
    for source_name in run_history:
        run_history[source_name].sort(key=lambda item: (item.started_at, str(item.id)))

    def run_summary(run: SyncRun | None) -> dict[str, Any] | None:
        if run is None:
            return None
        return {
            "started_at": run.started_at,
            "status": run.status,
            "matched": run.matched,
            "new": run.new,
            "updated": run.updated,
            "unchanged": run.unchanged,
            "ambiguous": run.ambiguous,
            "invalid_date": run.invalid_date,
        }
    source_records: dict[str, list[RaceSource]] = {
        source_name: [source for source in store.list_sources_for_source(source_name) if source.source_year == year]
        for source_name in (AIMS_NAME, WA_NAME)
    }
    distance_counts: dict[str, dict[str, int]] = {}
    for source_name, records in source_records.items():
        counts = {"marathon": 0, "half_marathon": 0, "other": 0}
        for record in records:
            distances = set(record.normalized_data.get("distance_types") or [])
            if "marathon" in distances:
                counts["marathon"] += 1
            if "half_marathon" in distances:
                counts["half_marathon"] += 1
            if not distances.intersection(MAIN_DISTANCE_TYPES):
                counts["other"] += 1
        distance_counts[source_name] = counts
    unresolved = [item for item in store.list_unresolved_source_records() if item.source_year == year and item.source_name in {AIMS_NAME, WA_NAME}]
    unresolved_by_source = {
        source_name: [item for item in unresolved if item.source_name == source_name]
        for source_name in (AIMS_NAME, WA_NAME)
    }
    discrepancies = [item for item in store.list_discrepancies() if (store.get_race(item.race_id) and store.get_race(item.race_id).year == year and item.source_name in {AIMS_NAME, WA_NAME})]
    matched_cross_source = 0
    china_cross_source = {AIMS_NAME: 0, WA_NAME: 0}
    for source_name, records in source_records.items():
        for record in records:
            race = store.get_race(record.race_id)
            if not race:
                continue
            names = {source.source_name for source in store.list_sources(race.id) if source.source_role is not SourceRole.planning_catalog}
            if len(names) >= 2:
                matched_cross_source += 1
            if race.country == "CHN":
                china_cross_source[source_name] += 1
    return {
        "year": year,
        "sources": {source_name: {
            "source_records": len(records),
            "records_fetched": latest_runs[source_name].list_records_fetched if source_name in latest_runs else None,
            "exact_date": sum(record.normalized_data.get("date_precision") == "exact" for record in records) + sum(item.date_precision == "exact" for item in unresolved_by_source[source_name]),
            "range_or_tbc": sum(item.date_precision != "exact" for item in unresolved_by_source[source_name]),
            "marathon": distance_counts[source_name]["marathon"],
            "half_marathon": distance_counts[source_name]["half_marathon"],
            "main_distance_relevant": sum(bool(set(record.normalized_data.get("distance_types") or []).intersection(MAIN_DISTANCE_TYPES)) for record in records),
            "other_distances": distance_counts[source_name]["other"],
             "matched_existing": latest_runs[source_name].matched if source_name in latest_runs else None,
             "new_canonical_races": latest_runs[source_name].new if source_name in latest_runs else None,
             "ambiguous": latest_runs[source_name].ambiguous if source_name in latest_runs else 0,
             "invalid": latest_runs[source_name].invalid_date if source_name in latest_runs else 0,
             "unresolved": len(unresolved_by_source[source_name]),
             "initial_import": run_summary(run_history[source_name][0] if run_history[source_name] else None),
             "latest_run": run_summary(latest_runs.get(source_name)),
         } for source_name, records in source_records.items()},
        "cross_source_matched_source_records": matched_cross_source,
        "china_cross_source_records": china_cross_source,
        "unresolved": {
            "total": len(unresolved),
            "tbc_date": sum(item.date_precision == "tbc" for item in unresolved),
            "multi_day": sum(item.date_precision == "range" for item in unresolved),
            "unknown_country": sum("unknown" in item.reason for item in unresolved),
        },
        "date_conflicts": {
            "total": len(discrepancies),
            "by_field": {field: sum(item.field_name == field for item in discrepancies) for field in sorted({item.field_name for item in discrepancies})},
        },
    }


def write_international_coverage_report(report: dict[str, Any], report_dir: str | Path = "reports") -> list[Path]:
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    year = report["year"]
    json_path = directory / f"international_coverage_{year}.json"
    md_path = directory / f"international_coverage_{year}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_serial) + "\n", encoding="utf-8")
    lines = [f"# International coverage — {year}", "", "| Source | Fetched | Persisted records | Exact | Range/TBC | Main M/H | Marathon | Half marathon | Other | Matched | New | Ambiguous | Invalid | Unresolved |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for source_name, values in report["sources"].items():
        lines.append(f"| {source_name} | {values['records_fetched'] if values['records_fetched'] is not None else '-'} | {values['source_records']} | {values['exact_date']} | {values['range_or_tbc']} | {values['main_distance_relevant']} | {values['marathon']} | {values['half_marathon']} | {values['other_distances']} | {values['matched_existing'] if values['matched_existing'] is not None else '-'} | {values['new_canonical_races'] if values['new_canonical_races'] is not None else '-'} | {values['ambiguous']} | {values['invalid']} | {values['unresolved']} |")
    lines.extend(["", "## Replay audit", ""])
    for source_name, values in report["sources"].items():
        initial = values.get("initial_import")
        latest = values.get("latest_run")
        lines.append(f"- {source_name}: initial new={initial['new'] if initial else '-'}, updated={initial['updated'] if initial else '-'}; latest matched={latest['matched'] if latest else '-'}, new={latest['new'] if latest else '-'}, updated={latest['updated'] if latest else '-'}, unchanged={latest['unchanged'] if latest else '-'}")
    lines.extend(["", "## Unresolved", "", f"- TBC date: {report['unresolved']['tbc_date']}", f"- Multi-day unresolved: {report['unresolved']['multi_day']}", f"- Unknown country: {report['unresolved']['unknown_country']}", "", "## Cross-source", "", f"- Cross-source matched Source Records: {report['cross_source_matched_source_records']}", f"- China + AIMS: {report['china_cross_source_records']['aims']}", f"- China + World Athletics: {report['china_cross_source_records']['world_athletics']}", f"- Date/field conflicts: {report['date_conflicts']['total']}", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return [json_path, md_path]
