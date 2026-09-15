from datetime import date

from marathon_calendar.domain.models import Race, RaceStatus, SyncStatus
from marathon_calendar.identity import canonical_identity_key
from marathon_calendar.repository import RaceStore
from marathon_calendar.snapshots import SnapshotStore
from marathon_calendar.sync import ChinaSyncService, SyncOptions
from marathon_calendar.sources.china_official import (
    DETAIL_ENDPOINT,
    LIST_ENDPOINT,
    ListFetchResult,
    ChinaOfficialSource,
    normalize_category,
    normalize_distances,
)


def list_row(external_id="1", name="2026南昌马拉松", race_date="2026-11-08"):
    return {
        "raceId": int(external_id),
        "raceName": name,
        "raceGrade": "A",
        "raceTime": race_date,
        "raceAddress": "江西省/南昌市/",
        "raceItem": '["全程", "半程"]',
        "raceScale": None,
    }


def detail(name="2026南昌马拉松", game_date="2026.11.08"):
    return {
        "success": True,
        "code": 0,
        "msg": "SUCCESS",
        "data": {
            "type": "SS",
            "ssdetails": {
                "name": name,
                "raceGrade": "A",
                "province": "江西省",
                "city": "南昌市",
                "gameDate": game_date,
                "project": "全程,半程",
                "compNameOrganizer": "南昌市人民政府",
            },
        },
    }


class FakeChinaSource(ChinaOfficialSource):
    def __init__(self):
        super().__init__(client=object())
        self.rows = [list_row(), list_row("2", "2026成都马拉松", "2026-10-25")]
        self.details = {"1": detail(), "2": detail("2026成都马拉松", "2026.10.25")}

    def fetch_race_list(self, *, year=None, page_size=100, filters=None, max_pages=None):
        rows = [row for row in self.rows if year is None or str(row["raceTime"]).startswith(str(year))]
        response = {"success": True, "code": 0, "data": {"results": rows, "pageCount": 1, "totalCount": len(rows)}}
        return ListFetchResult(
            pages=[response],
            all_records=list(self.rows),
            records=rows,
            page_count=1,
            total_count=len(self.rows),
            request_payload={"pageNo": 1, "pageSize": page_size},
        )

    def fetch_race_detail(self, external_id):
        return self.details[str(external_id)]


def service(tmp_path, source=None):
    return ChinaSyncService(
        RaceStore(tmp_path / "sync.db"),
        source or FakeChinaSource(),
        SnapshotStore(tmp_path / "snapshots"),
        sleeper=lambda _: None,
    )


def test_official_category_and_distance_normalization_preserve_source_text():
    assert normalize_category("C（属地办赛）") == ("C", "C（属地办赛）")
    assert normalize_category("中国田径协会主办系列赛") == ("TEN", "中国田径协会主办系列赛")
    assert normalize_distances('["全程", "半程", "欢乐跑"]') == (
        ["marathon", "half_marathon", "fun_run"],
        ["全程", "半程", "欢乐跑"],
    )


def test_sync_is_idempotent_and_creates_unique_source_records(tmp_path):
    sync = service(tmp_path)
    first = sync.sync(SyncOptions(year=2026, details_limit=2))
    assert first.status is SyncStatus.success
    assert first.list_records_fetched == 2
    assert first.created == 2
    assert first.details_fetched == 2
    assert len(sync.store.list_sources()) == 2
    old_races = {race.name: race for race in sync.store.list_races()}

    second = sync.sync(SyncOptions(year=2026, details_limit=2))
    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == 2
    assert second.details_fetched == 0
    assert len(sync.store.list_sources()) == 2
    assert len(sync.store.list_changes()) == 0
    assert {race.name: race.sequence for race in sync.store.list_races()} == {
        name: race.sequence for name, race in old_races.items()
    }


def test_date_change_creates_history_and_preserves_race_identity(tmp_path):
    source = FakeChinaSource()
    sync = service(tmp_path, source)
    sync.sync(SyncOptions(year=2026, details_limit=2))
    old_source = sync.store.get_source_by_external("china_official", "1")
    old_race = sync.store.get_race(old_source.race_id)

    source.rows[0] = list_row(race_date="2026-11-15")
    source.details["1"] = detail(game_date="2026.11.15")
    changed = sync.sync(SyncOptions(year=2026, details_limit=2))
    new_source = sync.store.get_source_by_external("china_official", "1")
    new_race = sync.store.get_race(new_source.race_id)
    assert changed.updated == 1
    assert new_race.id == old_race.id
    assert new_source.id == old_source.id
    assert new_race.race_date == date(2026, 11, 15)
    assert new_race.sequence == old_race.sequence + 1
    assert new_race.last_modified != old_race.last_modified
    assert any(change.field_name == "race_date" and change.scope == "race" for change in sync.store.list_changes())


def test_manual_override_survives_official_update_and_can_be_cleared(tmp_path):
    source = FakeChinaSource()
    sync = service(tmp_path, source)
    sync.sync(SyncOptions(year=2026, details_limit=2))
    source_record = sync.store.get_source_by_external("china_official", "1")
    sync.store.set_field_override(source_record.race_id, "race_date", "2026-11-15", "管理员确认日期")
    overridden = sync.sync(SyncOptions(year=2026, details_limit=2))
    race = sync.store.get_race(source_record.race_id)
    assert race.race_date == date(2026, 11, 15)
    assert overridden.updated == 1

    source.rows[0] = list_row(race_date="2026-11-22")
    source.details["1"] = detail(game_date="2026.11.22")
    sync.sync(SyncOptions(year=2026, details_limit=2))
    race = sync.store.get_race(source_record.race_id)
    source_record = sync.store.get_source_by_external("china_official", "1")
    assert race.race_date == date(2026, 11, 15)
    assert source_record.source_race_date == date(2026, 11, 22)
    assert any(change.scope == "race_source" and change.field_name == "race_date" for change in sync.store.list_changes())

    sync.store.clear_field_override(source_record.race_id, "race_date")
    sync.sync(SyncOptions(year=2026, details_limit=2))
    assert sync.store.get_race(source_record.race_id).race_date == date(2026, 11, 22)


def test_dry_run_writes_only_run_and_snapshot_not_domain_records(tmp_path):
    sync = service(tmp_path)
    run = sync.sync(SyncOptions(year=2026, details_limit=2, dry_run=True))
    assert run.created == 2
    assert sync.store.count() == 0
    assert sync.store.list_sources() == []
    assert sync.store.list_changes() == []
    assert len(sync.store.list_sync_runs()) == 1


def test_missing_source_records_are_flagged_after_three_complete_fetches(tmp_path):
    source = FakeChinaSource()
    sync = service(tmp_path, source)
    sync.sync(SyncOptions(year=2026, details_limit=2))
    source.rows = []
    source.details = {}
    for _ in range(3):
        run = sync.sync(SyncOptions(year=2026, details_limit=0))
    assert run.missing == 2
    assert sync.store.count() == 2
    assert all(item.flag_for_review for item in sync.store.list_sources_for_source("china_official"))
    assert all(race.status is RaceStatus.scheduled for race in sync.store.list_races())


def test_ambiguous_identity_creates_review_issue_without_auto_merge(tmp_path):
    source = FakeChinaSource()
    sync = service(tmp_path, source)
    first = sync.store
    for label in ("南昌马拉松", "2026南昌马拉松"):
        first.save_race(
            Race(
                name=label,
                year=2026,
                country="CHN",
                city="南昌",
                race_date=date(2026, 11, 8),
                distance_types=["marathon"],
                canonical_identity_key=canonical_identity_key(name=label, country="CHN", city="南昌"),
            )
        )
    source.rows = [list_row("9")]
    source.details = {"9": detail()}
    run = sync.sync(SyncOptions(year=2026, details_limit=1))
    assert run.ambiguous == 1
    assert len(sync.store.list_open_issues()) == 1
    assert len(sync.store.list_sources()) == 0
