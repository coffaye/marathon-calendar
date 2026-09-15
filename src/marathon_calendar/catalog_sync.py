from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .coverage import (
    AMBIGUOUS,
    CATALOG_ONLY,
    INVALID,
    MATCHED_DATE_CHANGED,
    MATCHED_EXACT,
    MATCHED_NAME_CHANGED,
    MATCHED_NORMALIZED,
    audit_catalog,
    build_coverage_report,
    write_coverage_report,
)
from .domain.models import (
    Race,
    RaceChange,
    RaceSource,
    RaceStatus,
    SourceRole,
    SyncRun,
    SyncStatus,
    VerificationStatus,
    utc_now,
)
from .identity import canonical_identity_key
from .repository import RaceStore
from .sources.china_annual_catalog import (
    AnnualCatalogRecord,
    CatalogFetchResult,
    CatalogSchemaError,
    ChinaAnnualCatalogSource,
    SOURCE_NAME,
)


MATCHED_RESULTS = {
    MATCHED_EXACT,
    MATCHED_NORMALIZED,
    MATCHED_DATE_CHANGED,
    MATCHED_NAME_CHANGED,
}


@dataclass
class CatalogSyncOptions:
    year: int
    dry_run: bool = False
    report_dir: str | Path = "reports"


@dataclass
class CatalogSyncResult:
    run: SyncRun
    audit: dict[str, Any]
    report: dict[str, Any]
    report_paths: tuple[Path, Path]


def _source_hash(record: AnnualCatalogRecord) -> str:
    value = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _catalog_source_data(record: AnnualCatalogRecord, fetch_result: CatalogFetchResult) -> dict[str, Any]:
    document_model = fetch_result.document
    return {
        "name": record.source_race_name,
        "race_date": record.source_race_date.isoformat() if record.source_race_date else None,
        "planned_date_text": record.planned_date_text,
        "province": record.province,
        "city": record.city,
        "distance_types": record.distance_types,
        "source_distance_text": record.source_distance_text,
        "association_level": record.association_level,
        "source_category_text": record.source_category_text,
        "organization": record.organization,
        "source_document": str(record.source_document),
        "source_document_checksum": record.source_document_checksum,
        "source_row_number": record.row_number,
        "source_page": record.source_page,
        "source_publication_date": record.source_publication_date.isoformat(),
        "source_document_retrieved_at": document_model.retrieved_at.isoformat(),
        "source_extraction_method": document_model.extraction_method,
    }


def _catalog_race(record: AnnualCatalogRecord) -> Race:
    if record.source_race_date is None:
        raise ValueError("a catalog-only Race requires an exact date")
    return Race(
        id=uuid4(),
        name=record.source_race_name,
        year=record.year,
        country="CHN",
        province=record.province,
        city=record.city,
        race_date=record.source_race_date,
        timezone="Asia/Shanghai",
        distance_types=record.distance_types,
        race_type="road_race",
        organization=record.organization,
        association_level=record.association_level,
        status=RaceStatus.date_tentative,
        canonical_identity_key=canonical_identity_key(
            name=record.source_race_name, country="CHN", city=record.city
        ),
        verification_status=VerificationStatus.planned,
        last_confirmed_at=None,
        confirmed_by_source_id=None,
    )


def _source_changes(old: RaceSource, new: RaceSource) -> list[tuple[str, Any, Any]]:
    fields = {
        "name",
        "race_date",
        "planned_date_text",
        "province",
        "city",
        "distance_types",
        "source_distance_text",
        "association_level",
        "source_category_text",
        "organization",
        "source_document_checksum",
    }
    return [
        (field, old.normalized_data.get(field), new.normalized_data.get(field))
        for field in fields
        if old.normalized_data.get(field) != new.normalized_data.get(field)
    ]


class ChinaAnnualCatalogSyncService:
    """Import the annual publication through RaceSource and conservative audit matches."""

    def __init__(self, store: RaceStore, source: ChinaAnnualCatalogSource):
        self.store = store
        self.source = source

    def _source_record(
        self,
        record: AnnualCatalogRecord,
        *,
        race_id: UUID,
        existing: RaceSource | None,
        is_authoritative: bool,
        now: datetime,
        fetch_result: CatalogFetchResult,
    ) -> RaceSource:
        normalized = _catalog_source_data(record, fetch_result)
        raw = {
            "document": {
                "url": str(record.source_document),
                "checksum": record.source_document_checksum,
                "publication_date": record.source_publication_date.isoformat(),
                "retrieved_at": fetch_result.document.retrieved_at.isoformat(),
                "extraction_method": fetch_result.document.extraction_method,
            },
            "row": record.raw_data,
        }
        return RaceSource(
            id=existing.id if existing else uuid4(),
            race_id=race_id,
            source_type="official_publication",
            source_name=SOURCE_NAME,
            source_url=record.source_document,
            external_id=record.external_id,
            source_race_name=record.source_race_name,
            source_race_date=record.source_race_date,
            raw_data=raw,
            source_hash=_source_hash(record),
            source_year=record.year,
            source_document=str(record.source_document),
            source_document_checksum=record.source_document_checksum,
            source_row_number=record.row_number,
            source_publication_date=record.source_publication_date,
            normalized_data=normalized,
            fetched_at=now,
            verified_at=now,
            last_seen_at=now,
            missing_count=0,
            flag_for_review=False,
            source_distance_text=record.source_distance_text,
            confidence=0.95,
            is_authoritative=False,
            source_role=SourceRole.planning_catalog,
            publishable=False,
            published_at=record.source_publication_date,
            retrieved_at=fetch_result.document.retrieved_at,
            last_confirmed_at=None,
            match_reason=(
                "annual catalog is planning evidence; API/organizer/current sources have higher authority"
            ),
        )

    def _target_for_catalog_only(
        self, record: AnnualCatalogRecord, existing: RaceSource | None
    ) -> tuple[Race | None, bool, str | None]:
        if existing:
            return self.store.get_race(existing.race_id), False, None
        candidates = self.store.find_races_by_identity(record.identity_key)
        if len(candidates) > 1:
            return None, False, "multiple canonical races share the catalog identity"
        if candidates:
            return candidates[0], False, None
        if record.source_race_date is None:
            return None, False, "planned date has no exact day; Race creation deferred"
        return _catalog_race(record), True, None

    def sync(self, options: CatalogSyncOptions) -> CatalogSyncResult:
        run = SyncRun(source=SOURCE_NAME, year=options.year, dry_run=options.dry_run)
        self.store.save_sync_run(run)
        fetch_result: CatalogFetchResult | None = None
        audit: dict[str, Any] = {"summary": {}, "records": [], "api_only": [], "api_only_total": 0, "api_total": 0}
        try:
            fetch_result = self.source.fetch(year=options.year)
            records = fetch_result.records
            run.list_records_total = len(records)
            run.list_records_fetched = len(records)
            run.normalized = len(records)
            run.missing_city = sum(item.city is None for item in records)
            run.unknown_distance = sum(not item.distance_types for item in records)
            run.unknown_category = sum(item.association_level not in {"A", "B", "C"} for item in records)
            api_sources = self.store.list_sources_by_role(SourceRole.live_official)
            audit = audit_catalog(records, api_sources)
            row_results = {row["row_number"]: row for row in audit["records"]}
            run.matched = sum(row["result"] in MATCHED_RESULTS for row in audit["records"])
            run.ambiguous = audit["summary"].get(AMBIGUOUS, 0)
            run.invalid_date = audit["summary"].get(INVALID, 0)
            now = utc_now()
            for record in records:
                row = row_results[record.row_number]
                existing = self.store.get_source_by_external(SOURCE_NAME, record.external_id)
                target_race: Race | None = None
                created_race = False
                target_error: str | None = None
                matched_race_ids = row.get("matched_race_ids", [])
                if row["result"] in MATCHED_RESULTS and matched_race_ids:
                    target_race = self.store.get_race(UUID(matched_race_ids[0]))
                elif row["result"] == CATALOG_ONLY:
                    target_race, created_race, target_error = self._target_for_catalog_only(record, existing)
                elif existing:
                    target_race = self.store.get_race(existing.race_id)
                if target_error:
                    if "exact day" in target_error:
                        continue
                    run.errors.append({"row_number": record.row_number, "message": target_error})
                    run.ambiguous += 1
                    continue
                if target_race is None:
                    run.errors.append({"row_number": record.row_number, "message": "matched Race not found"})
                    continue
                source = self._source_record(
                    record,
                    race_id=target_race.id,
                    existing=existing,
                    is_authoritative=False,
                    now=now,
                    fetch_result=fetch_result,
                )
                old_source_changes = _source_changes(existing, source) if existing else []
                if created_race:
                    run.new += 1
                    run.created += 1
                elif existing and old_source_changes:
                    run.updated += 1
                else:
                    run.unchanged += 1
                if options.dry_run:
                    continue
                if not created_race:
                    overrides = self.store.active_overrides(target_race.id)
                    # The API/organizer value is preserved. Catalog is a baseline only.
                    if not overrides:
                        target_race = target_race.model_copy(update={"last_verified_at": now, "updated_at": now})
                    else:
                        target_race = target_race.model_copy(update={"last_verified_at": now})
                changes = [
                    RaceChange(
                        race_id=target_race.id,
                        field_name=field,
                        old_value=old_value,
                        new_value=new_value,
                        source=SOURCE_NAME,
                        changed_at=now,
                        reason="annual catalog source normalized value changed",
                        sync_run_id=run.id,
                        scope="race_source",
                    )
                    for field, old_value, new_value in old_source_changes
                ]
                self.store.save_source_and_race(target_race, source, changes)
            if run.errors or run.ambiguous:
                run.status = SyncStatus.partial_success
            else:
                run.status = SyncStatus.success
        except (CatalogSchemaError, OSError, ValueError) as exc:
            run.status = SyncStatus.failed
            run.errors.append({"error_type": type(exc).__name__, "message": str(exc)})
        finally:
            run.finished_at = utc_now()
            self.store.finish_sync_run(run)
        if fetch_result is None:
            report = {"error": run.errors, "catalog_total": 0}
            paths = (Path(options.report_dir) / f"china_coverage_{options.year}.json", Path(options.report_dir) / f"china_coverage_{options.year}.md")
        else:
            attached = {
                source.external_id
                for source in self.store.list_sources_for_source(SOURCE_NAME)
                if source.source_year == options.year and source.external_id
            }
            report = build_coverage_report(
                fetch_result,
                audit,
                attached_catalog_external_ids=attached,
                canonical_race_count=self.store.count(),
            )
            paths = write_coverage_report(report, options.report_dir)
        return CatalogSyncResult(run=run, audit=audit, report=report, report_paths=paths)
