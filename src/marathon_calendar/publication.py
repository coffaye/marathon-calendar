"""Shared publication selectors used by FastAPI and the static exporter."""

from __future__ import annotations

from .international_sync import is_main_distance
from .realtime import is_formal_calendar_race, is_planned_calendar_race
from .repository import RaceStore


def races_for_feed(store: RaceStore, feed: str):
    if feed == "all":
        return store.list_races()
    if feed == "china":
        return [
            race
            for race in store.list_races(country="CHN")
            if is_formal_calendar_race(race, store.list_sources(race.id))
        ]
    if feed == "china-planned":
        return [
            race
            for race in store.list_races(country="CHN")
            if is_planned_calendar_race(race, store.list_sources(race.id))
        ]
    if feed == "full-marathon":
        return store.list_races(full_marathon_only=True)
    if feed == "world":
        return [
            race
            for race in store.list_races()
            if is_main_distance(race) and is_formal_calendar_race(race, store.list_sources(race.id))
        ]
    if feed == "international":
        return [
            race
            for race in store.list_races()
            if race.country != "CHN"
            and is_main_distance(race)
            and is_formal_calendar_race(race, store.list_sources(race.id))
        ]
    raise ValueError(f"unknown feed: {feed}")
