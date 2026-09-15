"""Adapter for the public AIMS subscription calendar.

The adapter intentionally parses the published RFC5545 text rather than the
rendered calendar page.  AIMS publishes a stable UID per event; that UID is
the source identity and is never replaced by a name/date hash.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from ..domain.models import RaceStatus, SourceRole
from ..identity import country_to_alpha3


SOURCE_NAME = "aims"
SOURCE_ROLE = SourceRole.international_calendar
PUBLISHABLE = True
CALENDAR_URL = "https://aims-worldrunning.org/events.ics"

_COUNTRY_NAMES = {
    "algeria": "DZA", "angola": "AGO", "argentina": "ARG", "australia": "AUS", "austria": "AUT",
    "barbados": "BAR", "belgium": "BEL", "bhutan": "BHU", "botswana": "BOT", "brazil": "BRA",
    "brunei": "BRU", "cambodia": "CAM", "canada": "CAN", "chile": "CHI", "china": "CHN",
    "chinese taipei": "TPE", "colombia": "COL", "costa rica": "CRC", "croatia": "CRO", "cuba": "CUB",
    "curacao": "CUW", "curaçao": "CUW", "cyprus": "CYP", "czech republic": "CZE", "denmark": "DEN",
    "dominican republic": "DOM", "ecuador": "ECU", "egypt": "EGY", "estonia": "EST", "ethiopia": "ETH",
    "faroe islands": "FRO", "finland": "FIN", "france": "FRA", "great britain": "GBR", "greece": "GRE",
    "greenland": "GRL", "germany": "GER", "hungary": "HUN", "india": "IND", "indonesia": "INA",
    "ireland": "IRL", "israel": "ISR", "italy": "ITA", "jamaica": "JAM", "japan": "JPN", "jordan": "JOR",
    "kazakhstan": "KAZ", "korea": "KOR", "south korea": "KOR", "kuwait": "KUW", "kyrgyzstan": "KGZ",
    "laos": "LAO", "lebanon": "LBN", "lithuania": "LTU", "luxembourg": "LUX", "malaysia": "MAS",
    "maldives": "MDV", "malta": "MLT", "macedonia": "MKD", "mexico": "MEX", "moldova": "MDA",
    "morocco": "MAR", "nepal": "NEP", "netherlands": "NED", "new zealand": "NZL", "norway": "NOR",
    "oman": "OMA", "panama": "PAN", "poland": "POL", "portugal": "POR", "puerto rico": "PUR",
    "qatar": "QAT", "romania": "ROU", "russia": "RUS", "rwanda": "RWA", "saudi arabia": "KSA",
    "serbia": "SRB", "singapore": "SGP", "slovakia": "SVK", "slovenia": "SLO", "spain": "ESP",
    "sri lanka": "SRI", "switzerland": "SUI", "taiwan": "TPE", "tajikistan": "TJK", "tanzania": "TAN",
    "thailand": "THA", "trinidad & tobago": "TTO", "tunisia": "TUN", "turkey": "TUR", "türkiye": "TUR",
    "ukraine": "UKR", "united arab emirates": "UAE", "united states of america": "USA", "usa": "USA",
    "uzbekistan": "UZB", "venezuela": "VEN", "vietnam": "VIE", "zimbabwe": "ZIM", "republic of china": "TPE",
}


class SourceSchemaError(ValueError):
    pass


class AimsEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    race_date: date | None = None
    end_date: date | None = None
    date_precision: str
    source_status: str | None = None
    status: RaceStatus | None = None
    location: str | None = None
    city: str | None = None
    country_raw: str | None = None
    country: str | None = None
    distance_types: list[str] = Field(default_factory=list)
    official_url: str | None = None
    description: str = ""
    sequence: int = 0
    last_modified: datetime | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


@dataclass
class AimsFetchResult:
    events: list[AimsEvent]
    raw_text: str
    fetched_at: datetime
    source_url: str
    document_checksum: str


def _unescape(value: str) -> str:
    # RFC5545 escaping is applied after line unfolding.
    return value.replace(r"\n", "\n").replace(r"\N", "\n").replace(r"\,", ",").replace(r"\;", ";").replace(r"\\", "\\")


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _properties(block: list[str]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for line in block:
        if ":" not in line:
            continue
        left, value = line.split(":", 1)
        name = left.split(";", 1)[0].upper()
        values.setdefault(name, []).append(_unescape(value))
    return values


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.match(r"^(\d{4})(\d{2})(\d{2})", value.strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    match = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z)?", value.strip())
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
            int(match.group(4)), int(match.group(5)), int(match.group(6)),
            tzinfo=timezone.utc if match.group(7) else None,
        )
    except ValueError:
        return None


def _status(summary: str, description: str, raw: str | None) -> tuple[str | None, RaceStatus | None]:
    combined = f"{summary} {description}".casefold()
    value = (raw or "").strip().upper() or None
    if value == "CANCELLED":
        return value, RaceStatus.cancelled
    if value == "TENTATIVE":
        return value, RaceStatus.date_tentative
    if re.search(r"\b(postponed|rescheduled)\b|延期|改期", combined):
        return value or "RESCHEDULED", RaceStatus.postponed
    return value, None


def _country_from_location(location: str | None) -> tuple[str | None, str | None, str | None]:
    if not location:
        return None, None, None
    parts = [part.strip() for part in location.split(",") if part.strip()]
    for part in reversed(parts):
        country = country_to_alpha3(part) or _COUNTRY_NAMES.get(part.casefold())
        if country:
            city = parts[-2] if len(parts) >= 2 else None
            return country, part, city
    return None, parts[-1] if parts else None, None


def _distances(summary: str, description: str) -> list[str]:
    text = f"{summary} {description}".casefold()
    result: list[str] = []
    def add(value: str) -> None:
        if value not in result:
            result.append(value)
    if re.search(r"\bhalf[ -]?marathon\b|\bsemi[ -]?marathon\b|\b21(?:\.1)?\s*k(?:m)?\b|半程", text):
        add("half_marathon")
    # Do not count the word in "half marathon" as a full marathon.
    if re.search(r"(?<!half )\bmarathon\b|\bmarat(?:h|ó|o)na\b|马拉松", text):
        add("marathon")
    if re.search(r"\b(?:10|10\.0)\s*k(?:m)?\b|\b10km\b", text):
        add("10k")
    if re.search(r"\b5\s*k(?:m)?\b|\b5km\b", text):
        add("5k")
    if re.search(r"\b(?:1|one)\s*mile\b", text):
        add("mile")
    if re.search(r"\bultra(?:marathon)?\b|超马", text):
        add("ultra")
    if re.search(r"\broad race\b|\broad running\b", text):
        add("road_race")
    return result


def parse_events(text: str) -> list[AimsEvent]:
    lines = _unfold(text)
    events: list[AimsEvent] = []
    block: list[str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            block = []
        elif line == "END:VEVENT":
            if block is None:
                continue
            props = _properties(block)
            uid = (props.get("UID") or [""])[0].strip()
            summary = (props.get("SUMMARY") or [""])[0].strip()
            if not uid or not summary:
                raise SourceSchemaError("AIMS VEVENT must contain UID and SUMMARY")
            start = _parse_date((props.get("DTSTART") or [None])[0])
            end_exclusive = _parse_date((props.get("DTEND") or [None])[0])
            end = end_exclusive - timedelta(days=1) if end_exclusive else start
            if any(re.search(r"\btbc\b", value, re.I) for key in ("SUMMARY", "LOCATION", "DESCRIPTION") for value in props.get(key, [])):
                precision = "tbc"
                start = None
                end = None
            elif start is None:
                precision = "tbc"
            elif end and end > start:
                precision = "range"
            else:
                precision = "exact"
                end = start
            description = "\n".join(props.get("DESCRIPTION", []))
            location = (props.get("LOCATION") or [None])[0]
            source_status, status = _status(summary, description, (props.get("STATUS") or [None])[0])
            url = (props.get("URL") or [None])[0]
            if url and not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
                url = "https://" + url
            country, country_name, city = _country_from_location(location)
            events.append(AimsEvent(
                uid=uid,
                summary=summary,
                race_date=start,
                end_date=end,
                date_precision=precision,
                source_status=source_status,
                status=status,
                location=location,
                city=city,
                country_raw=country_name,
                country=country,
                distance_types=_distances(summary, description),
                official_url=url,
                description=description,
                sequence=int((props.get("SEQUENCE") or ["0"])[0] or 0),
                last_modified=_parse_datetime((props.get("LAST-MODIFIED") or [None])[0]),
                raw_data={key: values for key, values in props.items()},
            ))
            block = None
        elif block is not None:
            block.append(line)
    return events


class AimsSource:
    def __init__(self, *, url: str = CALENDAR_URL, opener: Callable[..., Any] = urlopen, timeout: float = 30.0):
        self.url = url
        self.opener = opener
        self.timeout = timeout

    def fetch(self, *, year: int | None = None, document_path: str | Path | None = None, raw_text: str | None = None) -> AimsFetchResult:
        fetched_at = datetime.now(timezone.utc).replace(microsecond=0)
        if raw_text is None and document_path is not None:
            # Read bytes so the document checksum and saved snapshot reflect
            # the downloaded artifact, including its original line endings.
            raw_text = Path(document_path).read_bytes().decode("utf-8-sig")
        if raw_text is None:
            request = Request(self.url, headers={"Accept": "text/calendar", "User-Agent": "MarathonCalendar/0.3"})
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    raw_text = response.read().decode("utf-8-sig")
            except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
                raise SourceSchemaError(f"unable to fetch AIMS calendar: {exc}") from exc
        events = parse_events(raw_text)
        if year is not None:
            # A TBC item may intentionally have no parseable year/date left
            # after its source text is normalized. Keep it in the scoped
            # review set rather than silently dropping the evidence.
            events = [event for event in events if event.date_precision == "tbc" or (event.race_date and event.race_date.year == year) or (event.end_date and event.end_date.year == year)]
        return AimsFetchResult(
            events=events,
            raw_text=raw_text,
            fetched_at=fetched_at,
            source_url=self.url,
            document_checksum=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        )
