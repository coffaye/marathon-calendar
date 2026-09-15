from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Iterable

from .authority import highest_current_source, is_current_publishable_source, verification_for_race
from .domain.models import Race, RaceSource, RaceStatus


LIVE_CONFIRMED = "LIVE_CONFIRMED"
PLANNED_ONLY = "PLANNED_ONLY"
CANCELLED = "CANCELLED"
POSTPONED = "POSTPONED"
PAST_COMPLETED = "PAST_COMPLETED"
NEEDS_REVIEW = "NEEDS_REVIEW"


def classify_race(
    race: Race,
    sources: Iterable[RaceSource],
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> str:
    """Classify publication semantics without turning a plan into confirmation."""

    today = today or datetime.now(timezone.utc).date()
    source_list = list(sources)
    current = highest_current_source(source_list, now=now)
    if race.status is RaceStatus.cancelled:
        return CANCELLED
    if race.status is RaceStatus.postponed:
        return POSTPONED
    if current:
        explicit_completed = race.status is RaceStatus.completed or any(
            str(source.normalized_data.get("status") or "").casefold() == "completed"
            for source in source_list
        )
        if explicit_completed:
            return PAST_COMPLETED
        # A past planned date is not proof of completion. Keep it reviewable
        # unless a reliable source explicitly says completed.
        return LIVE_CONFIRMED if race.race_date >= today else NEEDS_REVIEW
    if any(source.source_role.value == "planning_catalog" for source in source_list):
        return PLANNED_ONLY
    return NEEDS_REVIEW


def is_formal_calendar_race(
    race: Race, sources: Iterable[RaceSource], *, now: datetime | None = None
) -> bool:
    return any(is_current_publishable_source(source, now=now) for source in sources)


def is_planned_calendar_race(
    race: Race, sources: Iterable[RaceSource], *, now: datetime | None = None
) -> bool:
    source_list = list(sources)
    return (
        any(source.source_role.value == "planning_catalog" for source in source_list)
        and not is_formal_calendar_race(race, source_list, now=now)
    )


def build_realtime_report(
    races: Iterable[Race],
    sources_by_race: dict[str, list[RaceSource]],
    *,
    year: int,
    today: date,
    formal_feed_count: int,
    planned_feed_count: int,
) -> dict[str, object]:
    in_scope = sorted((race for race in races if race.country == "CHN" and race.year == year), key=lambda race: (race.race_date, str(race.id)))
    classifications = {
        str(race.id): classify_race(race, sources_by_race.get(str(race.id), []), today=today)
        for race in in_scope
    }
    totals = Counter(classifications.values())
    future = [race for race in in_scope if race.race_date >= today]
    future_totals = Counter(classifications[str(race.id)] for race in future)
    return {
        "year": year,
        "as_of_date": today.isoformat(),
        "china_canonical_races": len(in_scope),
        "classification_totals": {key: totals.get(key, 0) for key in (LIVE_CONFIRMED, PLANNED_ONLY, CANCELLED, POSTPONED, PAST_COMPLETED, NEEDS_REVIEW)},
        "future_race_totals": {
            "future_races": len(future),
            "future_live_confirmed": future_totals.get(LIVE_CONFIRMED, 0),
            "future_planned_only": future_totals.get(PLANNED_ONLY, 0),
            "future_cancelled": future_totals.get(CANCELLED, 0),
            "future_postponed": future_totals.get(POSTPONED, 0),
            "future_unknown": future_totals.get(NEEDS_REVIEW, 0),
        },
        "feeds": {
            "china_ics_events": formal_feed_count,
            "china_planned_ics_events": planned_feed_count,
        },
        "live_calendar_coverage": {
            "denominator": None,
            "denominator_status": "unknown",
            "live_official_races": totals.get(LIVE_CONFIRMED, 0),
            "future_live_races": future_totals.get(LIVE_CONFIRMED, 0),
            "planned_only_future_races": future_totals.get(PLANNED_ONLY, 0),
        },
        "race_classifications": [
            {
                "race_id": str(race.id),
                "name": race.name,
                "race_date": race.race_date.isoformat(),
                "classification": classifications[str(race.id)],
                "verification_status": verification_for_race(
                    race, sources_by_race.get(str(race.id), []), now=None
                ).value,
                "source_roles": sorted({source.source_role.value for source in sources_by_race.get(str(race.id), [])}),
            }
            for race in in_scope
        ],
    }


def write_realtime_report(report: dict[str, object], output_dir: str | Path = "reports") -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    year = report["year"]
    json_path = output_dir / f"china_realtime_{year}.json"
    md_path = output_dir / f"china_realtime_{year}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    totals = report["classification_totals"]
    future = report["future_race_totals"]
    feeds = report["feeds"]
    lines = [
        f"# 中国赛事实时性校正（{year}）",
        "",
        f"截至：{report['as_of_date']}",
        "",
        "年度目录是 planning baseline / discovery source / historical evidence，不是 current confirmed calendar。官方公告使用“计划举办”“预计将举办”的规划语义；因此 `492 != 当前有效赛事总量`。",
        "",
        "## Race 分类",
        "",
        "| 分类 | 数量 |",
        "|---|---:|",
    ]
    for key in (LIVE_CONFIRMED, PLANNED_ONLY, CANCELLED, POSTPONED, PAST_COMPLETED, NEEDS_REVIEW):
        lines.append(f"| {key} | {totals[key]} |")
    lines.extend(
        [
            "",
            "## 未来赛事",
            "",
            f"- 未来赛事：{future['future_races']}",
            f"- Future live confirmed：{future['future_live_confirmed']}",
            f"- Future planned only：{future['future_planned_only']}",
            f"- Future cancelled：{future['future_cancelled']}",
            f"- Future postponed：{future['future_postponed']}",
            f"- Future unknown：{future['future_unknown']}",
            "",
            "## Feed",
            "",
            f"- `/calendar/china.ics`：{feeds['china_ics_events']} events",
            f"- `/calendar/china-planned.ics`：{feeds['china_planned_ics_events']} events",
            "",
            "## Live Calendar Coverage",
            "",
            "当前可靠实时赛事没有可证明的总量 denominator，因此不计算虚假百分比；报告可观测 live official、future live 和 planned-only 数量。",
            "",
            "完整逐 Race 分类见同名 JSON。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path
