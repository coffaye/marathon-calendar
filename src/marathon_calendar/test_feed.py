"""Deterministic, synthetic subscription event used only for client testing."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from .domain.models import Race, RaceStatus


def test_subscription_race(version: int | None = None) -> Race:
    if version is None:
        version = 2 if os.environ.get("MARATHON_CALENDAR_TEST_FEED_VERSION") == "2" else 1
    version = 2 if version == 2 else 1
    return Race(
        id=uuid5(NAMESPACE_URL, "https://marathon-calendar.local/test-subscription"),
        name=f"Marathon Calendar Subscription Test V{version}",
        name_en=f"Marathon Calendar Subscription Test V{version}",
        year=2026,
        country="USA",
        city="Test City",
        race_date=date(2026, 9, 21 if version == 2 else 20),
        timezone="UTC",
        distance_types=["Marathon"],
        status=RaceStatus.scheduled,
        canonical_identity_key="test-subscription-race",
        sequence=version - 1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, version, tzinfo=timezone.utc),
        last_modified=datetime(2026, 1, version, tzinfo=timezone.utc),
    )
