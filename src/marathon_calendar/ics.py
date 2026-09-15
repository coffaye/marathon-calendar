from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from .domain.models import Race, RaceStatus


def escape_text(value: str) -> str:
    """Escape iCalendar TEXT values in the order required by RFC 5545."""

    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold_line(line: str, limit: int = 75) -> str:
    """Fold an iCalendar content line at <=75 UTF-8 octets."""

    pieces: list[str] = []
    remaining = line
    first = True
    while remaining:
        budget = limit if first else limit - 1
        encoded = remaining.encode("utf-8")
        if len(encoded) <= budget:
            pieces.append(("" if first else " ") + remaining)
            break
        cut = budget
        while cut > 0:
            try:
                prefix = encoded[:cut].decode("utf-8")
                break
            except UnicodeDecodeError:
                cut -= 1
        if cut == 0:
            raise ValueError("Unable to fold iCalendar line")
        pieces.append(("" if first else " ") + prefix)
        remaining = remaining[len(prefix) :]
        first = False
    return "\r\n".join(pieces)


def _utc_basic(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _date_basic(value: date) -> str:
    return value.strftime("%Y%m%d")


def _status_value(status: RaceStatus) -> str:
    if status is RaceStatus.cancelled:
        return "CANCELLED"
    if status in {RaceStatus.postponed, RaceStatus.date_tentative}:
        return "TENTATIVE"
    return "CONFIRMED"


def _description(race: Race, *, planned: bool = False) -> str:
    pieces = [
        f"状态: {race.status.value}",
        f"项目: {', '.join(race.distance_types) if race.distance_types else '未注明'}",
    ]
    if planned:
        pieces.insert(0, "状态：年度计划，尚未通过当前赛事平台或组委会再次确认")
        pieces.append(f"计划日期：{race.race_date.isoformat()}")
        pieces.append("来源：中国田径协会年度赛事目录")
    if race.organization:
        pieces.append(f"组织方: {race.organization}")
    if race.association_level:
        pieces.append(f"协会等级: {race.association_level}")
    if race.world_athletics_label:
        pieces.append(f"World Athletics: {race.world_athletics_label}")
    if race.status is RaceStatus.postponed:
        pieces.append("赛事日期发生变更，请以官方公告为准。")
    if race.status is RaceStatus.cancelled:
        pieces.append("赛事已取消，保留此事件用于同步客户端状态。")
    return "\n".join(pieces)


def race_uid(race: Race) -> str:
    return f"race_{race.id}@marathon-calendar"


def race_to_vevent(race: Race, *, dtstamp: datetime, planned: bool = False) -> list[str]:
    end_date = race.race_date + timedelta(days=1)
    location_parts = [part for part in (race.province, race.city, race.country) if part]
    location = ", ".join(location_parts)
    summary = f"🟡 {race.name}（计划）" if planned else race.name
    if race.status is RaceStatus.cancelled:
        summary = f"[已取消] {summary}"
    elif race.status is RaceStatus.postponed:
        summary = f"[已改期] {summary}"

    lines = [
        "BEGIN:VEVENT",
        f"UID:{race_uid(race)}",
        f"SUMMARY:{escape_text(summary)}",
        f"DTSTART;VALUE=DATE:{_date_basic(race.race_date)}",
        f"DTEND;VALUE=DATE:{_date_basic(end_date)}",
        f"LOCATION:{escape_text(location)}",
        f"DESCRIPTION:{escape_text(_description(race, planned=planned))}",
    ]
    if race.official_url:
        lines.append(f"URL:{escape_text(str(race.official_url))}")
    lines.extend(
        [
            f"STATUS:{_status_value(race.status)}",
            f"SEQUENCE:{race.sequence}",
            f"LAST-MODIFIED:{_utc_basic(race.last_modified)}",
            f"DTSTAMP:{_utc_basic(dtstamp)}",
            "END:VEVENT",
        ]
    )
    return lines


def races_to_ics(
    races: Iterable[Race], *, calendar_name: str = "Marathon Calendar", now: datetime | None = None,
    planned: bool = False,
) -> str:
    stamp = now or datetime.now(timezone.utc).replace(microsecond=0)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Marathon Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(calendar_name)}",
    ]
    for race in sorted(races, key=lambda item: (item.race_date, item.name, str(item.id))):
        lines.extend(race_to_vevent(race, dtstamp=stamp, planned=planned))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_line(line) for line in lines) + "\r\n"
