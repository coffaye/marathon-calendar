from __future__ import annotations

import os
from datetime import date
from uuid import uuid5, NAMESPACE_URL

from .domain.models import Race, RaceSource, RaceStatus
from .identity import canonical_identity_key, identity_explanation
from .repository import RaceStore


def _demo_id(slug: str):
    return uuid5(NAMESPACE_URL, f"https://marathon-calendar.local/demo/{slug}")


def demo_races() -> list[Race]:
    definitions = [
        {
            "slug": "nanchang-marathon-2026",
            "name": "2026南昌马拉松",
            "name_en": "2026 Nanchang Marathon",
            "year": 2026,
            "country": "CHN",
            "province": "江西省",
            "city": "南昌",
            "race_date": date(2026, 11, 8),
            "distance_types": ["全程", "半程"],
            "association_level": "fixture",
            "status": RaceStatus.scheduled,
        },
        {
            "slug": "shanghai-marathon-2026",
            "name": "2026上海马拉松",
            "name_en": "2026 Shanghai Marathon",
            "year": 2026,
            "country": "CHN",
            "province": "上海市",
            "city": "上海",
            "race_date": date(2026, 12, 6),
            "distance_types": ["全程"],
            "association_level": "fixture",
            "status": RaceStatus.scheduled,
        },
        {
            "slug": "tokyo-marathon-2027",
            "name": "2027东京马拉松",
            "name_en": "2027 Tokyo Marathon",
            "year": 2027,
            "country": "JPN",
            "province": None,
            "city": "东京",
            "race_date": date(2027, 3, 7),
            "timezone": "Asia/Tokyo",
            "distance_types": ["全程"],
            "status": RaceStatus.date_tentative,
        },
        {
            "slug": "bangkok-marathon-2026",
            "name": "Bangkok Marathon 2026",
            "name_en": "Bangkok Marathon 2026",
            "year": 2026,
            "country": "THA",
            "province": None,
            "city": "Bangkok",
            "race_date": date(2026, 10, 25),
            "timezone": "Asia/Bangkok",
            "distance_types": ["Marathon", "Half Marathon"],
            "status": RaceStatus.postponed,
        },
        {
            "slug": "demo-cancelled",
            "name": "Demo Cancelled Road Race",
            "year": 2026,
            "country": "CHN",
            "province": "广东省",
            "city": "广州",
            "race_date": date(2026, 10, 18),
            "distance_types": ["全程"],
            "status": RaceStatus.cancelled,
        },
    ]
    races: list[Race] = []
    for item in definitions:
        item["id"] = _demo_id(item.pop("slug"))
        item["canonical_identity_key"] = canonical_identity_key(
            name=item["name"], country=item["country"], city=item["city"]
        )
        races.append(Race(**item))
    return races


def seed_demo(store: RaceStore) -> list[Race]:
    # Production must never create synthetic races, even if a caller invokes
    # this helper directly instead of going through create_app().
    if os.environ.get("MARATHON_CALENDAR_ENV", "development").strip().casefold() == "production":
        return store.list_races()
    if store.count():
        return store.list_races()
    races = demo_races()
    for race in races:
        store.save_race(race)
        store.add_source(
            RaceSource(
                race_id=race.id,
                source_type="fixture",
                source_name="Phase 1 Demo Fixture",
                source_url="https://example.invalid/marathon-calendar-phase-1",
                external_id=f"fixture:{race.id}",
                source_race_name=race.name,
                source_race_date=race.race_date,
                raw_data={"fixture": True, "note": "Not production data"},
                confidence=1.0,
                is_authoritative=True,
                match_reason=identity_explanation(race.name, race.name, country=race.country, city=race.city),
            )
        )
    return races
