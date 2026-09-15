import json
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from marathon_calendar.domain.models import Race, RaceAlias, RaceSource, ReconciliationIssue, SourceRole
from marathon_calendar.global_reconciliation import build_global_reconciliation_report
from marathon_calendar.identity import canonical_identity_key, country_to_alpha2, country_to_alpha3
from marathon_calendar.international_sync import InternationalSyncService
from marathon_calendar.main import create_app
from marathon_calendar.repository import RaceStore
from marathon_calendar.snapshots import SnapshotStore
from marathon_calendar.sources.aims import AimsEvent, AimsFetchResult, AimsSource, parse_events
from marathon_calendar.sources.world_athletics import WorldAthleticsFetchResult, WorldAthleticsSource, parse_html


AIMS_FIXTURE = r"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:aims-worldrunning.org-1
DTSTART;VALUE=DATE:20261004
DTEND;VALUE=DATE:20261005
STATUS:TENTATIVE
LAST-MODIFIED:20260901T120000Z
SEQUENCE:2
LOCATION:Japan
SUMMARY: Tokyo Marathon
URL:tokyomarathon.org
DESCRIPTION:Marathon and Half
END:VEVENT
BEGIN:VEVENT
UID:aims-worldrunning.org-2
DTSTART;VALUE=DATE:20261010
DTEND;VALUE=DATE:20261012
LOCATION:Japan
SUMMARY: Weekend Festival Marathon and 10K
END:VEVENT
END:VCALENDAR
"""


def _wa_fixture() -> str:
    payload = {
        "page": "/competitions/[competitionGroup]",
        "buildId": "fixture",
        "props": {"pageProps": {"calendarEvents": {"parameters": {"season": "2026"}, "results": [
            {"id": 123456, "name": "Tokyo Marathon", "venue": "Tokyo (JPN)", "venueWithoutCountry": "Tokyo", "country": "JPN", "countryCode": "JPN", "startDate": "2026-10-04", "endDate": "2026-10-04", "dateRange": "04 OCT 2026", "competitionSubgroup": "Platinum", "rankingCategory": "GW", "undeterminedCompetitionPeriod": None},
        ]}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"


def test_aims_parser_preserves_uid_sequence_status_and_does_not_fabricate_range():
    events = parse_events(AIMS_FIXTURE)
    assert events[0].uid == "aims-worldrunning.org-1"
    assert events[0].race_date == date(2026, 10, 4)
    assert events[0].date_precision == "exact"
    assert events[0].status.value == "date_tentative"
    assert events[0].sequence == 2
    assert events[1].date_precision == "range"
    assert events[1].race_date == date(2026, 10, 10)


def test_aims_parser_preserves_cancelled_postponed_and_tbc_semantics():
    text = r"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:cancelled-1
DTSTART;VALUE=DATE:20261001
DTEND;VALUE=DATE:20261002
STATUS:CANCELLED
SUMMARY:Cancelled Marathon
LOCATION:Tokyo, Japan
END:VEVENT
BEGIN:VEVENT
UID:postponed-1
DTSTART;VALUE=DATE:20261002
DTEND;VALUE=DATE:20261003
SUMMARY:Postponed Marathon
LOCATION:Tokyo, Japan
DESCRIPTION:Rescheduled by organizer
END:VEVENT
BEGIN:VEVENT
UID:tbc-1
DTSTART;VALUE=DATE:20261003
DTEND;VALUE=DATE:20261004
SUMMARY:Marathon date TBC
LOCATION:Tokyo, Japan
END:VEVENT
END:VCALENDAR
"""
    events = parse_events(text)
    assert events[0].status.value == "cancelled"
    assert events[1].status.value == "postponed"
    assert events[2].date_precision == "tbc"
    assert events[2].race_date is None
    fetched = AimsSource().fetch(year=2026, raw_text=text)
    assert len(fetched.events) == 3


def test_world_athletics_parser_uses_numeric_public_id_and_label():
    events, evidence = parse_html(_wa_fixture())
    assert events[0].competition_id == 123456
    assert events[0].official_url.endswith("/calendar-results/123456/result")
    assert events[0].label == "Platinum"
    assert evidence["record_count"] == 1


def test_country_conversions_are_explicit_and_unknown_is_not_guessed():
    assert country_to_alpha2("CHN") == "CN"
    assert country_to_alpha2("JPN") == "JP"
    assert country_to_alpha2("USA") == "US"
    assert country_to_alpha2("GBR") == "GB"
    assert country_to_alpha2("GER") == "DE"
    assert country_to_alpha3("CN") == "CHN"
    assert country_to_alpha3("not-a-country") is None


def test_aims_and_world_athletics_share_one_race_and_uid(tmp_path):
    store = RaceStore(tmp_path / "calendar.db")
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    aims_event = parse_events(AIMS_FIXTURE)[0]
    aims_fetch = AimsFetchResult([aims_event], AIMS_FIXTURE, now, "https://aims-worldrunning.org/events.ics", "aims-hash")
    aims = InternationalSyncService(store, source_name="aims", source_role=SourceRole.international_calendar, calendar_url="https://aims-worldrunning.org/events.ics", snapshots=SnapshotStore(tmp_path / "snapshots"))
    aims.sync(aims_fetch, year=2026, report_dir=tmp_path)
    wa_event = WorldAthleticsSource().fetch(raw_html=_wa_fixture(), year=2026).events[0]
    wa_fetch = WorldAthleticsFetchResult([wa_event], _wa_fixture(), now, "https://worldathletics.org/competitions/world-athletics-label-road-races", "wa-hash", {})
    wa = InternationalSyncService(store, source_name="world_athletics", source_role=SourceRole.international_federation, calendar_url="https://worldathletics.org/competitions/world-athletics-label-road-races", snapshots=SnapshotStore(tmp_path / "snapshots"))
    wa.sync(wa_fetch, year=2026, report_dir=tmp_path)
    races = store.list_races()
    assert len(races) == 1
    assert {source.source_name for source in store.list_sources(races[0].id)} == {"aims", "world_athletics"}
    assert races[0].timezone is None
    assert races[0].sequence == 1  # WA is the higher-authority source and fills the city/status fields.
    world_body = TestClient(create_app(store.db_path)).get("/calendar/world.ics").text
    world_uids = [line[4:] for line in world_body.splitlines() if line.startswith("UID:")]
    assert len(world_uids) == len(set(world_uids)) == 1
    replay = wa.sync(wa_fetch, year=2026, report_dir=tmp_path)
    assert replay.run.updated == 0
    assert replay.run.unchanged == 1
    assert store.list_races()[0].sequence == 1
    assert len(store.list_aliases()) == 2


def test_lower_authority_date_conflict_is_audit_only(tmp_path):
    store = RaceStore(tmp_path / "calendar.db")
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    wa_event = WorldAthleticsSource().fetch(raw_html=_wa_fixture(), year=2026).events[0]
    wa_fetch = WorldAthleticsFetchResult([wa_event], _wa_fixture(), now, "https://worldathletics.org/competitions/world-athletics-label-road-races", "wa-hash", {})
    wa = InternationalSyncService(store, source_name="world_athletics", source_role=SourceRole.international_federation, calendar_url="https://worldathletics.org/competitions/world-athletics-label-road-races", snapshots=SnapshotStore(tmp_path / "snapshots"))
    wa.sync(wa_fetch, year=2026, report_dir=tmp_path)
    aims_event = parse_events(AIMS_FIXTURE)[0].model_copy(update={"race_date": date(2026, 10, 5)})
    aims_fetch = AimsFetchResult([aims_event], AIMS_FIXTURE, now, "https://aims-worldrunning.org/events.ics", "aims-hash")
    aims = InternationalSyncService(store, source_name="aims", source_role=SourceRole.international_calendar, calendar_url="https://aims-worldrunning.org/events.ics", snapshots=SnapshotStore(tmp_path / "snapshots"))
    result = aims.sync(aims_fetch, year=2026, report_dir=tmp_path)
    race = store.list_races()[0]
    assert result.run.updated == 0
    assert race.race_date == date(2026, 10, 4)
    assert race.sequence == 0
    assert any(item.field_name == "race_date" for item in store.list_discrepancies())


def test_multiday_event_is_stored_as_unresolved_source_record(tmp_path):
    store = RaceStore(tmp_path / "calendar.db")
    event = parse_events(AIMS_FIXTURE)[1]
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    fetch = AimsFetchResult([event], AIMS_FIXTURE, now, "https://aims-worldrunning.org/events.ics", "hash")
    service = InternationalSyncService(store, source_name="aims", source_role=SourceRole.international_calendar, calendar_url="https://aims-worldrunning.org/events.ics", snapshots=SnapshotStore(tmp_path / "snapshots"))
    service.sync(fetch, year=2026, report_dir=tmp_path)
    assert store.list_races() == []
    unresolved = store.list_unresolved_source_records("aims")
    assert unresolved[0].date_precision == "range"
    assert unresolved[0].source_race_date == date(2026, 10, 10)
    first_seen = unresolved[0].first_seen_at
    service.sync(fetch, year=2026, report_dir=tmp_path)
    replayed = store.list_unresolved_source_records("aims")[0]
    assert replayed.first_seen_at == first_seen


def test_global_report_exposes_source_overlap_distribution(tmp_path):
    store = RaceStore(tmp_path / "calendar.db")
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    events = parse_events(AIMS_FIXTURE)[:1]
    aims_fetch = AimsFetchResult(events, AIMS_FIXTURE, now, "https://aims-worldrunning.org/events.ics", "hash")
    InternationalSyncService(store, source_name="aims", source_role=SourceRole.international_calendar, calendar_url="https://aims-worldrunning.org/events.ics", snapshots=SnapshotStore(tmp_path / "snapshots")).sync(aims_fetch, year=2026, report_dir=tmp_path)
    report = build_global_reconciliation_report(store, year=2026)
    assert report["source_coverage"]["distribution"]["single_source"] == 1
    assert report["identity"]["duplicate_clusters_count"] == 0


def test_explicit_merge_migrates_audit_references_without_deleting_loser(tmp_path):
    store = RaceStore(tmp_path / "calendar.db")
    survivor = Race(name="Boston Marathon", year=2026, country="USA", city="Boston", race_date=date(2026, 10, 11), canonical_identity_key="survivor")
    loser = Race(name="Boston Marathon duplicate", year=2026, country="USA", city="Boston", race_date=date(2026, 10, 11), canonical_identity_key="loser")
    store.save_race(survivor)
    store.save_race(loser)
    source = RaceSource(race_id=loser.id, source_type="fixture", source_name="fixture", source_url="https://example.com/race", source_race_name=loser.name, source_year=2026, confidence=0.5)
    store.add_source(source)
    store.add_alias(RaceAlias(race_id=loser.id, name=loser.name, normalized_name="boston"))
    store.set_field_override(loser.id, "name", "Boston Marathon curated", "merge test")
    issue = ReconciliationIssue(source_name="fixture", external_id="x", candidate_race_ids=[loser.id], reason="review")
    store.save_issue(issue)
    store.merge_races(survivor.id, loser.id, reason="explicit duplicate confirmation")
    assert store.get_race(loser.id).merged_into_id == survivor.id
    assert store.list_sources(survivor.id)[0].race_id == survivor.id
    assert store.list_aliases(survivor.id)[0].race_id == survivor.id
    assert store.active_overrides(survivor.id)["name"].override_value == "Boston Marathon curated"
    assert store.list_open_issues()[0].candidate_race_ids == [survivor.id]
