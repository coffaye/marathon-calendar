from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from .catalog_sync import CatalogSyncOptions, ChinaAnnualCatalogSyncService
from .coverage import audit_catalog, build_coverage_report, write_coverage_report
from .domain.models import SourceRole
from .global_reconciliation import build_global_reconciliation_report, write_global_reconciliation_report
from .international_sync import InternationalSyncService, build_international_coverage_report, write_international_coverage_report
from .export import export_site, export_test_feed
from .repository import RaceStore
from .realtime import (
    build_realtime_report,
    is_formal_calendar_race,
    is_planned_calendar_race,
    write_realtime_report,
)
from .snapshots import SnapshotStore
from .sync import ChinaSyncService, SyncOptions
from .sources.china_annual_catalog import ChinaAnnualCatalogSource
from .sources.aims import AimsSource, CALENDAR_URL as AIMS_CALENDAR_URL, SOURCE_NAME as AIMS_SOURCE_NAME, SOURCE_ROLE as AIMS_SOURCE_ROLE
from .sources.world_athletics import WorldAthleticsSource, CALENDAR_URL as WA_CALENDAR_URL, SOURCE_NAME as WA_SOURCE_NAME, SOURCE_ROLE as WA_SOURCE_ROLE
from .state import export_state, import_state


def _db_path(value: str | None) -> Path:
    return Path(value or os.environ.get("MARATHON_CALENDAR_DB", "data/marathon_calendar.db"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marathon-calendar")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="synchronize an external source")
    sync_subparsers = sync_parser.add_subparsers(dest="source", required=True)
    china = sync_subparsers.add_parser("china", help="synchronize China official calendar")
    china.add_argument("--year", type=int, required=False)
    china.add_argument("--page-size", type=int, default=100)
    china.add_argument(
        "--details-limit",
        type=int,
        default=0,
        help="maximum detail requests; 0 means all records that need details",
    )
    china.add_argument("--dry-run", action="store_true")
    china.add_argument("--db", default=None)
    china.add_argument("--snapshot-root", default="data/snapshots")
    aims = sync_subparsers.add_parser("aims", help="synchronize the public AIMS events.ics calendar")
    aims.add_argument("--year", type=int, required=False)
    aims.add_argument("--document", default=None, help="local raw events.ics snapshot; omitted means fetch the official URL")
    aims.add_argument("--report-dir", default="reports")
    aims.add_argument("--snapshot-root", default="data/snapshots")
    aims.add_argument("--dry-run", action="store_true")
    aims.add_argument("--db", default=None)
    wa = sync_subparsers.add_parser("world-athletics", help="synchronize the public World Athletics Label Road Races page")
    wa.add_argument("--year", type=int, required=False)
    wa.add_argument("--document", default=None, help="local raw HTML snapshot; omitted means fetch the official page")
    wa.add_argument("--report-dir", default="reports")
    wa.add_argument("--snapshot-root", default="data/snapshots")
    wa.add_argument("--dry-run", action="store_true")
    wa.add_argument("--db", default=None)
    international = sync_subparsers.add_parser("international", help="synchronize AIMS and World Athletics")
    international.add_argument("--year", type=int, required=False)
    international.add_argument("--aims-document", default=None)
    international.add_argument("--world-athletics-document", default=None)
    international.add_argument("--report-dir", default="reports")
    international.add_argument("--snapshot-root", default="data/snapshots")
    international.add_argument("--dry-run", action="store_true")
    international.add_argument("--db", default=None)
    catalog = sync_subparsers.add_parser("china-catalog", help="import the China Athletics Association annual catalog")
    catalog.add_argument("--year", type=int, required=True)
    catalog.add_argument("--document", default=None, help="normalized JSON sidecar for the official PDF")
    catalog.add_argument("--report-dir", default="reports")
    catalog.add_argument("--dry-run", action="store_true")
    catalog.add_argument("--db", default=None)

    audit_parser = subparsers.add_parser("audit", help="audit source coverage and reconciliation")
    audit_subparsers = audit_parser.add_subparsers(dest="audit_type", required=True)
    coverage_parser = audit_subparsers.add_parser("china-coverage", help="audit China annual catalog coverage")
    coverage_parser.add_argument("--year", type=int, required=True)
    coverage_parser.add_argument("--document", default=None, help="normalized JSON sidecar for the official PDF")
    coverage_parser.add_argument("--report-dir", default="reports")
    coverage_parser.add_argument("--db", default=None)
    realtime_parser = audit_subparsers.add_parser("china-realtime", help="classify current China feed semantics")
    realtime_parser.add_argument("--year", type=int, required=True)
    realtime_parser.add_argument("--report-dir", default="reports")
    realtime_parser.add_argument("--db", default=None)
    global_parser = audit_subparsers.add_parser("global-reconciliation", help="audit China/AIMS/World Athletics reconciliation")
    global_parser.add_argument("--year", type=int, required=True)
    global_parser.add_argument("--report-dir", default="reports")
    global_parser.add_argument("--db", default=None)

    runs = subparsers.add_parser("runs", help="show recent synchronization runs")
    runs.add_argument("--limit", type=int, default=20)
    runs.add_argument("--db", default=None)

    state = subparsers.add_parser("state", help="export or import version-controlled canonical state")
    state_subparsers = state.add_subparsers(dest="state_action", required=True)
    state_export = state_subparsers.add_parser("export")
    state_export.add_argument("--db", default=None)
    state_export.add_argument("--output", default="state")
    state_import_parser = state_subparsers.add_parser("import")
    state_import_parser.add_argument("--state", default="state")
    state_import_parser.add_argument("--db", required=True)
    state_import_parser.add_argument("--replace", action="store_true")

    export_parser = subparsers.add_parser("export-site", help="build a static GitHub Pages site")
    export_parser.add_argument("--db", default=None)
    export_parser.add_argument("--output", default="site")
    export_parser.add_argument("--base-url", default=None)
    export_parser.add_argument("--test-feed-version", type=int, choices=(1, 2), default=1)

    test_feed_parser = subparsers.add_parser("test-feed", help="write the synthetic V1/V2 subscription feed")
    test_feed_parser.add_argument("--version", type=int, choices=(1, 2), required=True)
    test_feed_parser.add_argument("--output", default="site/calendar/test-subscription.ics")

    override = subparsers.add_parser("override", help="manage a manual field override")
    override_subparsers = override.add_subparsers(dest="override_action", required=True)
    set_parser = override_subparsers.add_parser("set")
    set_parser.add_argument("race_id")
    set_parser.add_argument("field")
    set_parser.add_argument("value")
    set_parser.add_argument("--reason", required=True)
    set_parser.add_argument("--db", default=None)
    clear_parser = override_subparsers.add_parser("clear")
    clear_parser.add_argument("race_id")
    clear_parser.add_argument("field")
    clear_parser.add_argument("--db", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sync" and args.source == "china":
        store = RaceStore(_db_path(args.db))
        service = ChinaSyncService(store, snapshots=SnapshotStore(args.snapshot_root))
        run = service.sync(
            SyncOptions(
                year=args.year,
                page_size=args.page_size,
                details_limit=args.details_limit,
                dry_run=args.dry_run,
            )
        )
        print(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 1 if run.status.value == "failed" else 0
    if args.command == "sync" and args.source == "china-catalog":
        document = Path(args.document or f"data/source_documents/china_annual_catalog_{args.year}.pdf")
        store = RaceStore(_db_path(args.db))
        result = ChinaAnnualCatalogSyncService(store, ChinaAnnualCatalogSource(document)).sync(
            CatalogSyncOptions(year=args.year, dry_run=args.dry_run, report_dir=args.report_dir)
        )
        print(
            json.dumps(
                {
                    "run": result.run.model_dump(mode="json"),
                    "result_totals": result.report.get("result_totals", {}),
                    "coverage": result.report.get("coverage", {}),
                    "reports": [str(path) for path in result.report_paths],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if result.run.status.value == "failed" else 0
    if args.command == "sync" and args.source in {"aims", "world-athletics"}:
        store = RaceStore(_db_path(args.db))
        if args.source == "aims":
            fetch = AimsSource().fetch(year=args.year, document_path=args.document)
            service = InternationalSyncService(store, source_name=AIMS_SOURCE_NAME, source_role=AIMS_SOURCE_ROLE, calendar_url=AIMS_CALENDAR_URL, snapshots=SnapshotStore(args.snapshot_root))
        else:
            fetch = WorldAthleticsSource().fetch(year=args.year, document_path=args.document)
            service = InternationalSyncService(store, source_name=WA_SOURCE_NAME, source_role=WA_SOURCE_ROLE, calendar_url=WA_CALENDAR_URL, snapshots=SnapshotStore(args.snapshot_root))
        result = service.sync(fetch, year=args.year, dry_run=args.dry_run, report_dir=args.report_dir)
        aggregate_paths = [] if args.dry_run or args.year is None else write_international_coverage_report(build_international_coverage_report(store, year=args.year), args.report_dir)
        print(json.dumps({"run": result.run.model_dump(mode="json"), "report": result.report, "reports": [str(path) for path in result.report_paths + aggregate_paths]}, ensure_ascii=False, indent=2))
        return 1 if result.run.status.value == "failed" else 0
    if args.command == "sync" and args.source == "international":
        store = RaceStore(_db_path(args.db))
        results = []
        aims_fetch = AimsSource().fetch(year=args.year, document_path=args.aims_document)
        aims_result = InternationalSyncService(store, source_name=AIMS_SOURCE_NAME, source_role=AIMS_SOURCE_ROLE, calendar_url=AIMS_CALENDAR_URL, snapshots=SnapshotStore(args.snapshot_root)).sync(aims_fetch, year=args.year, dry_run=args.dry_run, report_dir=args.report_dir)
        results.append(aims_result)
        wa_fetch = WorldAthleticsSource().fetch(year=args.year, document_path=args.world_athletics_document)
        wa_result = InternationalSyncService(store, source_name=WA_SOURCE_NAME, source_role=WA_SOURCE_ROLE, calendar_url=WA_CALENDAR_URL, snapshots=SnapshotStore(args.snapshot_root)).sync(wa_fetch, year=args.year, dry_run=args.dry_run, report_dir=args.report_dir)
        results.append(wa_result)
        aggregate_paths = [] if args.dry_run or args.year is None else write_international_coverage_report(build_international_coverage_report(store, year=args.year), args.report_dir)
        print(json.dumps({"runs": [item.run.model_dump(mode="json") for item in results], "reports": [str(path) for item in results for path in item.report_paths + aggregate_paths]}, ensure_ascii=False, indent=2))
        return 1 if any(item.run.status.value == "failed" for item in results) else 0
    if args.command == "audit" and args.audit_type == "china-coverage":
        document = Path(args.document or f"data/source_documents/china_annual_catalog_{args.year}.pdf")
        store = RaceStore(_db_path(args.db))
        try:
            fetch_result = ChinaAnnualCatalogSource(document).fetch(year=args.year)
            audit = audit_catalog(fetch_result.records, store.list_sources_by_role(SourceRole.live_official))
            attached = {
                source.external_id
                for source in store.list_sources_for_source("china_annual_catalog")
                if source.source_year == args.year and source.external_id
            }
            report = build_coverage_report(
                fetch_result,
                audit,
                attached_catalog_external_ids=attached,
                canonical_race_count=store.count(),
            )
            paths = write_coverage_report(report, args.report_dir)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(
            json.dumps(
                {
                    "catalog_total": report["catalog_total"],
                    "api_total": report["api_total"],
                    "result_totals": report["result_totals"],
                    "api_only_total": report["api_only_total"],
                    "coverage": report["coverage"],
                    "reports": [str(path) for path in paths],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "audit" and args.audit_type == "china-realtime":
        from datetime import datetime, timezone

        store = RaceStore(_db_path(args.db))
        now = datetime.now(timezone.utc)
        races = store.list_races(country="CHN")
        sources_by_race = {str(race.id): store.list_sources(race.id) for race in races}
        formal_count = sum(
            is_formal_calendar_race(race, sources_by_race[str(race.id)], now=now) for race in races
        )
        planned_count = sum(
            is_planned_calendar_race(race, sources_by_race[str(race.id)], now=now) for race in races
        )
        report = build_realtime_report(
            races,
            sources_by_race,
            year=args.year,
            today=now.date(),
            formal_feed_count=formal_count,
            planned_feed_count=planned_count,
        )
        paths = write_realtime_report(report, args.report_dir)
        print(
            json.dumps(
                {
                    "classification_totals": report["classification_totals"],
                    "future_race_totals": report["future_race_totals"],
                    "feeds": report["feeds"],
                    "reports": [str(path) for path in paths],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "audit" and args.audit_type == "global-reconciliation":
        store = RaceStore(_db_path(args.db))
        report = build_global_reconciliation_report(store, year=args.year)
        paths = write_global_reconciliation_report(report, args.report_dir)
        print(json.dumps({"status": report["status"], "source_coverage": report["source_coverage"], "identity": report["identity"], "discrepancies": report["discrepancies"]["total"], "reports": [str(path) for path in paths]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "runs":
        store = RaceStore(_db_path(args.db))
        print(json.dumps([run.model_dump(mode="json") for run in store.list_sync_runs(args.limit)], ensure_ascii=False, indent=2))
        return 0
    if args.command == "state" and args.state_action == "export":
        store = RaceStore(_db_path(args.db))
        print(json.dumps(export_state(store, args.output), ensure_ascii=False, indent=2))
        return 0
    if args.command == "state" and args.state_action == "import":
        result = import_state(args.state, args.db, replace=args.replace)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "export-site":
        store = RaceStore(_db_path(args.db))
        result = export_site(
            store,
            args.output,
            base_url=args.base_url or os.environ.get("BASE_URL", ""),
            test_feed_version=args.test_feed_version,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "test-feed":
        print(json.dumps(export_test_feed(args.version, args.output), ensure_ascii=False, indent=2))
        return 0
    if args.command == "override":
        store = RaceStore(_db_path(args.db))
        race_id = UUID(args.race_id)
        if args.override_action == "set":
            override = store.set_field_override(race_id, args.field, args.value, args.reason)
            print(json.dumps(override.model_dump(mode="json"), ensure_ascii=False, indent=2))
        else:
            store.clear_field_override(race_id, args.field)
            print(json.dumps({"cleared": True, "race_id": args.race_id, "field": args.field}, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
