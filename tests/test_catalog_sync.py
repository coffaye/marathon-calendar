from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from marathon_calendar.catalog_sync import CatalogSyncOptions, ChinaAnnualCatalogSyncService
from marathon_calendar.coverage import (
    AMBIGUOUS,
    CATALOG_ONLY,
    MATCHED_DATE_CHANGED,
    MATCHED_NAME_CHANGED,
    audit_catalog,
)
from marathon_calendar.domain.models import Race, RaceSource, RaceStatus
from marathon_calendar.identity import canonical_identity_key
from marathon_calendar.ics import race_uid
from marathon_calendar.repository import RaceStore
from marathon_calendar.snapshots import SnapshotStore
from marathon_calendar.sources.china_annual_catalog import (
    AnnualCatalogRecord,
    CatalogDocument,
    CatalogFetchResult,
)
from marathon_calendar.sources.china_official import ChinaOfficialSource, ListFetchResult
from marathon_calendar.sync import ChinaSyncService, SyncOptions
from marathon_calendar.main import create_app


URL = "https://example.com/2026-catalog.pdf"
CHECKSUM = "a" * 64


def catalog_record(
    *,
    row_number=1,
    name="2026南昌马拉松",
    race_date=date(2026, 11, 8),
    province="江西省",
    distances=None,
):
    distances = distances or ["marathon"]
    return AnnualCatalogRecord(
        row_number=row_number,
        source_page=2,
        year=2026,
        source_race_name=name,
        source_race_date=race_date,
        planned_date_text="11月8日",
        province=province,
        organization="测试主办单位",
        distance_types=distances,
        source_distance_text=["全程"],
        association_level="A",
        source_category_text="A",
        external_id=f"catalog:2026:test-{row_number}-{name}",
        source_document=URL,
        source_document_checksum=CHECKSUM,
        source_publication_date=date(2025, 12, 19),
        raw_data={"row_number": row_number},
    )


class FakeCatalogSource:
    def __init__(self, records):
        self.records = records

    def fetch(self, *, year=None):
        return CatalogFetchResult(
            document=CatalogDocument(
                title="test catalog",
                year=2026,
                source_url=URL,
                publication_date=date(2025, 12, 19),
                retrieved_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
                document_checksum=CHECKSUM,
                extraction_method="test",
            ),
            records=self.records,
        )


def api_source(store, *, name="2026南昌马拉松", race_date=date(2026, 11, 8), external_id="api-1"):
    race = Race(
        name=name,
        year=2026,
        country="CHN",
        province="江西省",
        city="南昌市",
        race_date=race_date,
        distance_types=["marathon"],
        canonical_identity_key=canonical_identity_key(name=name, country="CHN", city="南昌市"),
    )
    store.save_race(race)
    source = RaceSource(
        race_id=race.id,
        source_type="official_association",
        source_name="china_official",
        source_url=f"https://www.runchina.org.cn/race/v/detail/{external_id}",
        external_id=external_id,
        source_race_name=name,
        source_race_date=race_date,
        source_year=2026,
        normalized_data={
            "name": name,
            "race_date": race_date.isoformat(),
            "province": "江西省",
            "city": "南昌市",
            "distance_types": ["marathon"],
        },
        confidence=0.98,
        is_authoritative=True,
    )
    store.add_source(source)
    return race, source


def test_catalog_only_creates_tentative_race_and_is_idempotent(tmp_path):
    store = RaceStore(tmp_path / "catalog.db")
    service = ChinaAnnualCatalogSyncService(store, FakeCatalogSource([catalog_record()]))
    first = service.sync(CatalogSyncOptions(year=2026, report_dir=tmp_path / "reports"))
    race = store.list_races()[0]
    stable_uid = race_uid(race)
    source = store.get_source_by_external("china_annual_catalog", catalog_record().external_id)
    assert first.run.status.value == "success"
    assert first.audit["summary"][CATALOG_ONLY] == 1
    assert race.status is RaceStatus.date_tentative
    assert source.race_id == race.id
    assert source.is_authoritative is False
    assert source.source_row_number == 1
    assert source.source_document_checksum == CHECKSUM

    second = service.sync(CatalogSyncOptions(year=2026, report_dir=tmp_path / "reports"))
    assert second.run.created == 0
    assert second.run.updated == 0
    assert len(store.list_sources_for_source("china_annual_catalog")) == 1
    assert store.list_changes() == []
    assert race_uid(store.list_races()[0]) == stable_uid


def test_catalog_match_preserves_api_values_and_records_date_name_categories(tmp_path):
    store = RaceStore(tmp_path / "match.db")
    api_race, _ = api_source(store, race_date=date(2026, 11, 15))
    date_changed = catalog_record(race_date=date(2026, 11, 8))
    audit = audit_catalog([date_changed], store.list_sources_for_source("china_official"))
    assert audit["records"][0]["result"] == MATCHED_DATE_CHANGED
    result = ChinaAnnualCatalogSyncService(store, FakeCatalogSource([date_changed])).sync(
        CatalogSyncOptions(year=2026, report_dir=tmp_path / "reports")
    )
    annual = store.get_source_by_external("china_annual_catalog", date_changed.external_id)
    assert annual.race_id == api_race.id
    assert annual.is_authoritative is False
    assert store.get_race(api_race.id).race_date == date(2026, 11, 15)
    assert result.report["date_changes_found"]

    renamed = catalog_record(row_number=2, name="2026南昌锦江国际马拉松")
    api_name_race, _ = api_source(store, name="2026南昌锦江国马拉松", external_id="api-2")
    name_audit = audit_catalog([renamed], [store.get_source_by_external("china_official", "api-2")])
    assert name_audit["records"][0]["result"] == MATCHED_NAME_CHANGED
    assert api_name_race.id != api_race.id


def test_ambiguous_candidates_are_reported_without_merge(tmp_path):
    store = RaceStore(tmp_path / "ambiguous.db")
    first, first_source = api_source(store, external_id="api-1")
    second, second_source = api_source(store, external_id="api-2")
    audit = audit_catalog(
        [catalog_record()],
        [first_source, second_source],
    )
    assert audit["summary"][AMBIGUOUS] == 1
    assert set(audit["records"][0]["matched_api_external_ids"]) == {"api-1", "api-2"}
    assert first.id != second.id


class LaterApiSource(ChinaOfficialSource):
    def __init__(self, game_date="2026-11-15"):
        super().__init__(client=object())
        self.game_date = game_date

    def fetch_race_list(self, *, year=None, page_size=100, filters=None, max_pages=None):
        row = {
            "raceId": 99,
            "raceName": "2026南昌马拉松",
            "raceGrade": "A",
            "raceTime": self.game_date.replace("-", "."),
            "raceAddress": "江西省/南昌市/",
            "raceItem": '["全程"]',
            "raceScale": None,
        }
        response = {"success": True, "code": 0, "data": {"results": [row], "pageCount": 1, "totalCount": 1}}
        return ListFetchResult(
            pages=[response],
            all_records=[row],
            records=[row],
            page_count=1,
            total_count=1,
            request_payload={"pageNo": 1, "pageSize": page_size},
        )

    def fetch_race_detail(self, external_id):
        return {
            "success": True,
            "code": 0,
            "data": {
                "type": "SS",
                "ssdetails": {
                    "name": "2026南昌马拉松",
                    "raceGrade": "A",
                    "province": "江西省",
                    "city": "南昌市",
                    "gameDate": self.game_date,
                    "project": "全程",
                    "compNameOrganizer": "南昌市人民政府",
                },
            },
        }


def test_later_api_attaches_to_catalog_race_and_manual_override_wins(tmp_path):
    store = RaceStore(tmp_path / "later-api.db")
    record = catalog_record()
    ChinaAnnualCatalogSyncService(store, FakeCatalogSource([record])).sync(
        CatalogSyncOptions(year=2026, report_dir=tmp_path / "reports")
    )
    catalog_source = store.get_source_by_external("china_annual_catalog", record.external_id)
    original_race_id = catalog_source.race_id
    stable_uid = race_uid(store.get_race(original_race_id))
    store.set_field_override(original_race_id, "race_date", "2026-11-22", "人工确认")
    api_sync = ChinaSyncService(
        store,
        LaterApiSource(),
        SnapshotStore(tmp_path / "snapshots"),
        sleeper=lambda _: None,
    )
    api_sync.sync(SyncOptions(year=2026, details_limit=1))
    api_source_record = store.get_source_by_external("china_official", "99")
    assert api_source_record.race_id == original_race_id
    assert store.get_race(original_race_id).race_date == date(2026, 11, 22)
    assert store.get_source_by_external("china_annual_catalog", record.external_id).is_authoritative is False
    assert store.get_race(original_race_id).id == original_race_id
    assert race_uid(store.get_race(original_race_id)) == stable_uid


def test_planning_catalog_is_only_in_planned_feed_until_live_api_appears(tmp_path):
    store = RaceStore(tmp_path / "feed.db")
    record = catalog_record()
    catalog_result = ChinaAnnualCatalogSyncService(store, FakeCatalogSource([record])).sync(
        CatalogSyncOptions(year=2026, report_dir=tmp_path / "reports")
    )
    race_id = store.get_source_by_external("china_annual_catalog", record.external_id).race_id
    client = TestClient(create_app(store.db_path))
    formal_before = client.get("/calendar/china.ics")
    planned_before = client.get("/calendar/china-planned.ics")
    assert formal_before.status_code == 200
    assert record.source_race_name not in formal_before.text
    assert f"🟡 {record.source_race_name}（计划）" in planned_before.text
    assert "中国马拉松年度规划（含未确认赛事）" in planned_before.text
    assert len(client.get("/races?verification=planned").json()) == 1
    assert catalog_result.report["coverage"]["annual_catalog_api_enrichment_count"] == 0

    ChinaSyncService(
        store,
        LaterApiSource(game_date="2026-11-15"),
        SnapshotStore(tmp_path / "snapshots"),
        sleeper=lambda _: None,
    ).sync(SyncOptions(year=2026, details_limit=1))
    formal_after = client.get("/calendar/china.ics")
    planned_after = client.get("/calendar/china-planned.ics")
    assert record.source_race_name in formal_after.text
    assert "DTSTART;VALUE=DATE:20261115" in formal_after.text
    assert record.source_race_name not in planned_after.text
    assert len(client.get("/races?verification=confirmed").json()) == 1
    assert store.get_source_by_external("china_annual_catalog", record.external_id).source_race_date == date(2026, 11, 8)
    assert store.get_race(race_id).id == race_id


def test_planning_retrieval_is_not_confirmation_and_publication_is_separate(tmp_path):
    store = RaceStore(tmp_path / "timestamps.db")
    record = catalog_record()
    result = ChinaAnnualCatalogSyncService(store, FakeCatalogSource([record])).sync(
        CatalogSyncOptions(year=2026, report_dir=tmp_path / "reports")
    )
    source = store.get_source_by_external("china_annual_catalog", record.external_id)
    assert source.published_at == date(2025, 12, 19)
    assert source.retrieved_at == datetime(2026, 9, 15, tzinfo=timezone.utc)
    assert source.last_confirmed_at is None
    assert store.get_race(source.race_id).last_confirmed_at is None
    assert result.report["document"]["publication_date"] == "2025-12-19"
    assert result.report["document"]["retrieved_at"] == "2026-09-15T00:00:00Z"
