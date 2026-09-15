from datetime import date, datetime, timezone
from uuid import uuid4

from marathon_calendar.domain.models import Race, RaceStatus
from marathon_calendar.ics import escape_text, race_uid, races_to_ics


def make_race(**overrides) -> Race:
    values = {
        "id": uuid4(),
        "name": "南昌;马拉松, Demo",
        "name_en": "Nanchang Marathon",
        "year": 2026,
        "country": "CHN",
        "province": "江西省",
        "city": "南昌",
        "race_date": date(2026, 11, 8),
        "distance_types": ["全程"],
        "canonical_identity_key": "demo",
        "official_url": "https://example.com/race/1",
    }
    values.update(overrides)
    return Race(**values)


def test_calendar_and_event_structure_and_crlf():
    ics = races_to_ics([make_race()], now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.endswith("END:VCALENDAR\r\n")
    assert "BEGIN:VEVENT\r\n" in ics
    assert "END:VEVENT\r\n" in ics
    assert "PRODID:-//Marathon Calendar//EN" in ics
    assert "DTSTART;VALUE=DATE:20261108" in ics
    assert "DTEND;VALUE=DATE:20261109" in ics
    assert "DTSTAMP:20260915T000000Z" in ics
    assert "\n" not in ics.replace("\r\n", "")


def test_text_escaping_and_chinese_are_preserved():
    assert escape_text(r"a\\b;c,d\ne") == r"a\\\\b\;c\,d\\ne"
    ics = races_to_ics([make_race()])
    assert "SUMMARY:南昌\\;马拉松\\, Demo" in ics
    assert "LOCATION:江西省\\, 南昌\\, CHN" in ics


def test_uid_is_stable_and_date_update_has_sequence_and_last_modified():
    original = make_race()
    updated = original.model_copy(
        update={
            "race_date": date(2026, 11, 15),
            "sequence": 1,
            "last_modified": datetime(2026, 9, 16, 1, 2, 3, tzinfo=timezone.utc),
        }
    )
    old_ics = races_to_ics([original], now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    new_ics = races_to_ics([updated], now=datetime(2026, 9, 16, tzinfo=timezone.utc))
    assert race_uid(original) == race_uid(updated)
    assert f"UID:{race_uid(original)}" in new_ics
    assert "DTSTART;VALUE=DATE:20261108" in old_ics
    assert "DTSTART;VALUE=DATE:20261115" in new_ics
    assert "DTEND;VALUE=DATE:20261116" in new_ics
    assert "SEQUENCE:1" in new_ics
    assert "LAST-MODIFIED:20260916T010203Z" in new_ics


def test_cancelled_and_postponed_statuses_are_not_deleted():
    cancelled = make_race(status=RaceStatus.cancelled)
    postponed = make_race(status=RaceStatus.postponed, id=uuid4())
    ics = races_to_ics([cancelled, postponed])
    assert "STATUS:CANCELLED" in ics
    assert "STATUS:TENTATIVE" in ics
    assert "SUMMARY:[已取消]" in ics
    assert "SUMMARY:[已改期]" in ics
    assert "赛事已取消" in ics
    assert "赛事日期发生变更" in ics


def test_long_line_is_folded_at_utf8_octet_boundary():
    long_race = make_race(name="中文赛事" * 80)
    ics = races_to_ics([long_race])
    for line in ics.split("\r\n"):
        if line and not line.startswith(" "):
            assert len(line.encode("utf-8")) <= 75

