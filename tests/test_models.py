from datetime import date
from uuid import uuid4

import pytest

from marathon_calendar.domain.models import Race, RaceSource, RaceStatus
from marathon_calendar.identity import canonical_identity_key
from marathon_calendar.repository import RaceStore


def race() -> Race:
    return Race(
        id=uuid4(),
        name="南昌马拉松",
        year=2026,
        country="CHN",
        city="南昌",
        race_date=date(2026, 11, 8),
        distance_types=["全程", "半程"],
        canonical_identity_key=canonical_identity_key(name="南昌马拉松", country="CHN", city="南昌"),
    )


def test_race_creation_and_timezone_validation():
    item = race()
    assert item.race_date == date(2026, 11, 8)
    assert item.timezone == "Asia/Shanghai"
    with pytest.raises(ValueError):
        Race(
            name="Invalid timezone",
            year=2026,
            country="CHN",
            race_date=date(2026, 1, 1),
            timezone="Not/AZone",
            canonical_identity_key="invalid",
        )


def test_multiple_sources_and_authoritative_override(tmp_path):
    store = RaceStore(tmp_path / "test.db")
    item = race()
    store.save_race(item)
    first = RaceSource(
        race_id=item.id,
        source_type="official_association",
        source_name="CAA",
        source_url="https://www.runchina.org.cn/",
        external_id="1001",
        source_race_name=item.name,
        source_race_date=item.race_date,
        confidence=0.95,
        is_authoritative=True,
    )
    second = RaceSource(
        race_id=item.id,
        source_type="aims",
        source_name="AIMS",
        source_url="https://aims-worldrunning.org/events.ics",
        external_id="aims-1",
        source_race_name="Nanchang Marathon",
        source_race_date=item.race_date,
        confidence=0.85,
        is_authoritative=True,
    )
    store.add_source(first)
    store.add_source(second)
    sources = store.list_sources(item.id)
    assert len(sources) == 2
    assert [source.is_authoritative for source in sources].count(True) == 1
    assert next(source for source in sources if source.id == second.id).is_authoritative


def test_date_and_status_update_increment_sequence(tmp_path):
    store = RaceStore(tmp_path / "test.db")
    item = race()
    store.save_race(item)
    updated = store.update_race(item.id, race_date=date(2026, 11, 15), status=RaceStatus.postponed)
    assert updated.id == item.id
    assert updated.race_date == date(2026, 11, 15)
    assert updated.status is RaceStatus.postponed
    assert updated.sequence == item.sequence + 1
