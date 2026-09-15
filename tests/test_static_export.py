from __future__ import annotations

from marathon_calendar.export import export_site
from marathon_calendar.ics import races_to_ics
from marathon_calendar.main import create_app
from marathon_calendar.publication import races_for_feed
from marathon_calendar.repository import RaceStore
from marathon_calendar.state import export_state, import_state


def test_state_export_is_deterministic_and_round_trips_identity(tmp_path):
    source_store = create_app(tmp_path / "source.db").state.store
    race = source_store.list_races()[0]
    source_store.set_field_override(race.id, "name", "Reviewed race name", "phase4 round-trip test")
    first_state = tmp_path / "state-a"
    second_state = tmp_path / "state-b"
    export_state(source_store, first_state)
    export_state(source_store, second_state)
    assert sorted(path.name for path in first_state.iterdir()) == sorted(path.name for path in second_state.iterdir())
    assert all(
        (first_state / path.name).read_bytes() == (second_state / path.name).read_bytes()
        for path in first_state.iterdir()
    )

    imported = tmp_path / "imported.db"
    import_state(first_state, imported)
    imported_store = RaceStore(imported)
    roundtrip_state = tmp_path / "state-roundtrip"
    export_state(imported_store, roundtrip_state)
    assert all(
        (first_state / path.name).read_bytes() == (roundtrip_state / path.name).read_bytes()
        for path in first_state.iterdir()
    )
    before = {race.id: (race.sequence, race.last_modified) for race in source_store.list_all_races()}
    after = {race.id: (race.sequence, race.last_modified) for race in imported_store.list_all_races()}
    assert before == after
    assert [
        (source.race_id, source.source_name, source.external_id)
        for source in source_store.list_sources()
    ] == [
        (source.race_id, source.source_name, source.external_id)
        for source in imported_store.list_sources()
    ]
    assert source_store.list_all_overrides() == imported_store.list_all_overrides()


def test_static_export_reuses_fastapi_feed_semantics(tmp_path, monkeypatch):
    monkeypatch.setenv("MARATHON_CALENDAR_ENV", "development")
    store = create_app(tmp_path / "source.db").state.store
    output = tmp_path / "site"
    status = export_site(store, output, base_url="https://owner.github.io/repo")

    assert status["feeds"]["world"]["events"] >= 0
    assert status["feeds"]["international"]["events"] >= 0
    assert (output / "data" / "status.json").exists()
    assert (output / "data" / "feeds.json").exists()
    assert (output / "favicon.svg").exists()
    for name, calendar_name, planned in (
        ("china", "中国马拉松赛事日历", False),
        ("china-planned", "中国马拉松年度规划（含未确认赛事）", True),
        ("international", "国际马拉松赛事日历", False),
        ("world", "全球马拉松赛事日历", False),
        ("full-marathon", "Marathon Calendar — Full Marathon", False),
    ):
        races = list(races_for_feed(store, name))
        expected = races_to_ics(
            races,
            calendar_name=calendar_name,
            now=max((race.last_modified for race in races), default=None),
            planned=planned,
        )
        assert (output / "calendar" / f"{name}.ics").read_bytes().decode("utf-8") == expected
    test_text = (output / "calendar" / "test-subscription.ics").read_bytes().decode("utf-8")
    assert "SEQUENCE:0" in test_text
    assert "world.ics" not in test_text
