"""World Athletics Label Road Races public-page adapter.

The page is server-rendered and currently embeds the same public calendar
records in ``__NEXT_DATA__``.  We parse that embedded JSON, retain the raw
HTML, and reject a page that no longer exposes the expected schema.  No
private API or anti-bot bypass is used.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from ..domain.models import RaceStatus, SourceRole
from ..identity import country_to_alpha3


SOURCE_NAME = "world_athletics"
SOURCE_ROLE = SourceRole.international_federation
PUBLISHABLE = True
CALENDAR_URL = "https://worldathletics.org/competitions/world-athletics-label-road-races"


class SourceSchemaError(ValueError):
    pass


class WorldAthleticsEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competition_id: int = Field(gt=0)
    name: str = Field(min_length=1)
    race_date: date | None = None
    end_date: date | None = None
    date_precision: str
    city: str | None = None
    country_raw: str | None = None
    country: str | None = None
    label: str | None = None
    ranking_category: str | None = None
    distance_types: list[str] = Field(default_factory=list)
    status: RaceStatus | None = None
    source_status: str | None = None
    official_url: str
    raw_data: dict[str, Any] = Field(default_factory=dict)


@dataclass
class WorldAthleticsFetchResult:
    events: list[WorldAthleticsEvent]
    raw_html: str
    fetched_at: datetime
    source_url: str
    document_checksum: str
    evidence: dict[str, Any]


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _distance(name: str) -> list[str]:
    text = name.casefold()
    result: list[str] = []
    if re.search(r"\bhalf marathon\b|\bhalfmarathon\b|\b21(?:\.1)?k\b|medio marat", text):
        result.append("half_marathon")
    if re.search(r"(?<!half )\bmarathon\b|maratón|maratona", text):
        result.append("marathon")
    if re.search(r"\b10k\b|\b10 km\b|10km", text):
        result.append("10k")
    if re.search(r"\b5k\b|\b5 km\b|5km", text):
        result.append("5k")
    if re.search(r"\bmile\b", text):
        result.append("mile")
    if not result:
        result.append("road_race")
    return result


def _status(name: str, raw: Any) -> tuple[str | None, RaceStatus | None]:
    raw_value = str(raw).strip() if raw not in (None, "") else None
    text = f"{name} {raw_value or ''}".casefold()
    if "cancel" in text:
        return raw_value or "Cancelled", RaceStatus.cancelled
    if "postpon" in text or "reschedul" in text:
        return raw_value or "Postponed", RaceStatus.postponed
    return raw_value, None


def _next_data(html: str) -> dict[str, Any]:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.I | re.S)
    if not match:
        raise SourceSchemaError("World Athletics page has no __NEXT_DATA__ script")
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise SourceSchemaError("World Athletics __NEXT_DATA__ is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SourceSchemaError("World Athletics page data must be an object")
    return value


def parse_html(html: str, *, source_url: str = CALENDAR_URL) -> tuple[list[WorldAthleticsEvent], dict[str, Any]]:
    data = _next_data(html)
    try:
        page = data["props"]["pageProps"]
        calendar = page["calendarEvents"]
        records = calendar["results"]
    except (KeyError, TypeError) as exc:
        raise SourceSchemaError("World Athletics page schema missing pageProps.calendarEvents.results") from exc
    if not isinstance(records, list):
        raise SourceSchemaError("World Athletics calendar results must be an array")
    events: list[WorldAthleticsEvent] = []
    base = source_url.rstrip("/")
    for raw in records:
        if not isinstance(raw, dict):
            raise SourceSchemaError("World Athletics calendar result is not an object")
        try:
            competition_id = int(raw["id"])
            name = str(raw["name"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceSchemaError("World Athletics result requires numeric id and name") from exc
        start = _date(raw.get("startDate"))
        end = _date(raw.get("endDate")) or start
        precision = "tbc" if start is None else ("range" if end and end != start else "exact")
        status_raw, status = _status(name, raw.get("undeterminedCompetitionPeriod"))
        country_raw = str(raw.get("countryCode") or raw.get("country") or "").strip() or None
        venue = str(raw.get("venueWithoutCountry") or raw.get("venue") or "").strip() or None
        official_url = f"{base}/calendar-results/{competition_id}/result"
        events.append(WorldAthleticsEvent(
            competition_id=competition_id,
            name=name,
            race_date=start,
            end_date=end,
            date_precision=precision,
            city=venue,
            country_raw=country_raw,
            country=country_to_alpha3(country_raw),
            label=str(raw.get("competitionSubgroup") or "").strip() or None,
            ranking_category=str(raw.get("rankingCategory") or "").strip() or None,
            distance_types=_distance(name),
            status=status,
            source_status=status_raw,
            official_url=official_url,
            raw_data=raw,
        ))
    evidence = {
        "page": data.get("page"),
        "build_id": data.get("buildId"),
        "calendar_parameters": calendar.get("parameters", {}),
        "record_count": len(events),
        "fields_observed": sorted({key for item in records if isinstance(item, dict) for key in item}),
    }
    return events, evidence


class WorldAthleticsSource:
    def __init__(self, *, url: str = CALENDAR_URL, opener: Callable[..., Any] = urlopen, timeout: float = 45.0):
        self.url = url
        self.opener = opener
        self.timeout = timeout

    def fetch(self, *, year: int | None = None, document_path: str | Path | None = None, raw_html: str | None = None) -> WorldAthleticsFetchResult:
        fetched_at = datetime.now(timezone.utc).replace(microsecond=0)
        if raw_html is None and document_path is not None:
            raw_html = Path(document_path).read_text(encoding="utf-8", errors="replace")
        if raw_html is None:
            request = Request(self.url, headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0 MarathonCalendar/0.3"})
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    raw_html = response.read().decode("utf-8", errors="replace")
            except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
                raise SourceSchemaError(f"unable to fetch World Athletics page: {exc}") from exc
        events, evidence = parse_html(raw_html, source_url=self.url)
        if year is not None:
            events = [event for event in events if event.date_precision == "tbc" or (event.race_date and event.race_date.year == year) or (event.end_date and event.end_date.year == year)]
        return WorldAthleticsFetchResult(
            events=events,
            raw_html=raw_html,
            fetched_at=fetched_at,
            source_url=self.url,
            document_checksum=hashlib.sha256(raw_html.encode("utf-8")).hexdigest(),
            evidence=evidence,
        )
