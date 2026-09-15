"""Cross-source coverage and reconciliation audit for the global calendar."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID

from .domain.models import SourceRole
from .identity import country_to_alpha2
from .repository import RaceStore


def _source_group(source_name: str) -> str:
    if source_name == "china_official":
        return "china"
    if source_name == "aims":
        return "aims"
    if source_name == "world_athletics":
        return "world_athletics"
    return source_name


def build_global_reconciliation_report(store: RaceStore, *, year: int) -> dict[str, Any]:
    races = [race for race in store.list_races() if race.year == year]
    by_race: dict[str, list[Any]] = {str(race.id): store.list_sources(race.id) for race in races}
    source_sets: dict[str, set[str]] = {}
    for race in races:
        source_sets[str(race.id)] = {
            _source_group(source.source_name)
            for source in by_race[str(race.id)]
            if source.source_role is not SourceRole.planning_catalog
        }
    source_sets = {key: value for key, value in source_sets.items() if value}
    distribution = {
        "single_source": sum(len(value) == 1 for value in source_sets.values()),
        "two_sources": sum(len(value) == 2 for value in source_sets.values()),
        "three_or_more_sources": sum(len(value) >= 3 for value in source_sets.values()),
    }
    pairs = {
        "china_aims": sum({"china", "aims"}.issubset(value) for value in source_sets.values()),
        "china_world_athletics": sum({"china", "world_athletics"}.issubset(value) for value in source_sets.values()),
        "aims_world_athletics": sum({"aims", "world_athletics"}.issubset(value) for value in source_sets.values()),
        "all_three": sum({"china", "aims", "world_athletics"}.issubset(value) for value in source_sets.values()),
    }
    identity_groups: dict[str, list[Any]] = defaultdict(list)
    for race in races:
        identity_groups[race.canonical_identity_key].append(race)
    candidate_clusters = [
        {
            "canonical_identity_key": key,
            "race_ids": [str(race.id) for race in sorted(items, key=lambda item: str(item.id))],
            "names": [race.name for race in items],
        }
        for key, items in sorted(identity_groups.items())
        if len(items) > 1
    ]
    # The identity normalizer deliberately removes distance words.  A full
    # marathon and a half marathon in the same weekend therefore form a
    # reviewable event family, not an automatic duplicate.  A hard duplicate
    # requires a shared distance and the same effective race day.
    duplicate_clusters = []
    festival_family_candidates = []
    for cluster in candidate_clusters:
        items = [store.get_race(UUID(race_id)) for race_id in cluster["race_ids"]]
        items = [item for item in items if item is not None]
        hard = False
        for index, left in enumerate(items):
            for right in items[index + 1:]:
                if left.race_date == right.race_date and set(left.distance_types).intersection(right.distance_types):
                    hard = True
        (duplicate_clusters if hard else festival_family_candidates).append(cluster)
    unresolved = [record for record in store.list_unresolved_source_records() if record.source_year == year]
    open_issues = store.list_open_issues()
    discrepancies = [item for item in store.list_discrepancies() if (store.get_race(item.race_id) and store.get_race(item.race_id).year == year)]

    by_group: dict[str, list[Any]] = defaultdict(list)
    for race in races:
        groups = sorted(source_sets.get(str(race.id), set()))
        for group in groups:
            by_group[group].append(race)
    samples: dict[str, list[dict[str, Any]]] = {}
    for group in ("aims", "world_athletics"):
        items = [race for race in by_group.get(group, []) if source_sets.get(str(race.id), set()) == {group}]
        samples[f"{group}_only"] = [_sample(race, source_sets[str(race.id)]) for race in sorted(items, key=lambda item: (item.race_date, str(item.id)))[:10]]
    cross = [race for race in races if len(source_sets.get(str(race.id), set())) >= 2]
    samples["cross_source"] = [_sample(race, source_sets[str(race.id)]) for race in sorted(cross, key=lambda item: (item.race_date, str(item.id)))[:30]]

    non_planning_sources = [
        source
        for sources in by_race.values()
        for source in sources
        if source.source_role is not SourceRole.planning_catalog
    ]
    source_record_counts = {
        group: sum(_source_group(source.source_name) == group for source in non_planning_sources)
        for group in ("china", "aims", "world_athletics")
    }
    # This is an auditable lower bound, not a claim about hypothetical unseen
    # duplicates: each source record attached to a Race that already has a
    # different non-planning source is one cross-source duplicate prevented.
    duplicates_prevented = sum(
        1
        for sources in by_race.values()
        for source in sources
        if source.source_role is not SourceRole.planning_catalog
        and len({
            _source_group(item.source_name)
            for item in sources
            if item.source_role is not SourceRole.planning_catalog
        }) >= 2
    )

    return {
        "year": year,
        "source_coverage": {
            "canonical_races_total": len(races),
            "canonical_races_with_non_planning_source": len(source_sets),
            "distribution": distribution,
            "pair_overlap": pairs,
            "source_record_counts": source_record_counts,
            "canonical_race_counts": {
                group: sum(group in source_sets.get(str(race.id), set()) for race in races)
                for group in ("china", "aims", "world_athletics")
            },
        },
        "identity": {
            "duplicate_clusters": duplicate_clusters,
            "duplicate_clusters_count": len(duplicate_clusters),
            "festival_family_candidates": festival_family_candidates,
            "festival_family_candidates_count": len(festival_family_candidates),
            "ambiguous_open_issues": len(open_issues),
            "unresolved_source_records": len(unresolved),
            "duplicates_prevented": duplicates_prevented,
        },
        "discrepancies": {
            "total": len(discrepancies),
            "by_field": {field: sum(item.field_name == field for item in discrepancies) for field in sorted({item.field_name for item in discrepancies})},
            "items": [item.model_dump(mode="json") for item in discrepancies],
        },
        "manual_review": {
            "ambiguous": [item.model_dump(mode="json") for item in open_issues],
            "date_conflicts": [item.model_dump(mode="json") for item in discrepancies],
            "possible_false_merges": festival_family_candidates,
            "tbc": [item.model_dump(mode="json") for item in unresolved if item.date_precision == "tbc"],
            "multi_day": [item.model_dump(mode="json") for item in unresolved if item.date_precision == "range"],
            "unknown_country": [item.model_dump(mode="json") for item in unresolved if "unknown" in item.reason],
        },
        "samples": {
            **samples,
            "required_shape": {"aims_only": 10, "world_athletics_only": 10, "cross_source_minimum": 10},
            "cross_source_minimum_met": len(samples["cross_source"]) >= 10,
        },
        "status": "PASS" if not duplicate_clusters and not open_issues else "PARTIAL",
    }


def _sample(race: Any, source_names: set[str]) -> dict[str, Any]:
    return {
        "race_id": str(race.id),
        "name": race.name,
        "date": race.race_date.isoformat(),
        "country": race.country,
        "country_alpha2": country_to_alpha2(race.country),
        "source_names": sorted(source_names),
        "canonical_uid": f"race_{race.id}@marathon-calendar",
    }


def write_global_reconciliation_report(report: dict[str, Any], report_dir: str | Path) -> list[Path]:
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    year = report["year"]
    json_path = directory / f"global_reconciliation_{year}.json"
    md_path = directory / f"global_reconciliation_{year}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    coverage = report["source_coverage"]
    md_path.write_text("\n".join([
        f"# Global reconciliation — {year}", "", f"Status: **{report['status']}**", "",
        "## Source coverage", "", "| Metric | Count |", "|---|---:|",
        f"| Active canonical races total | {coverage['canonical_races_total']} |",
        f"| Canonical races with non-planning source | {coverage['canonical_races_with_non_planning_source']} |",
        f"| Single source | {coverage['distribution']['single_source']} |",
        f"| Two sources | {coverage['distribution']['two_sources']} |",
        f"| Three or more sources | {coverage['distribution']['three_or_more_sources']} |", "",
        "| Overlap | Count |", "|---|---:|", *[f"| {key} | {value} |" for key, value in coverage["pair_overlap"].items()], "",
        "## Identity audit", "", f"- Duplicate clusters: {report['identity']['duplicate_clusters_count']}", f"- Cross-source Source Records linked into a multi-source Race (duplicates prevented): {report['identity']['duplicates_prevented']}", f"- Ambiguous open issues: {report['identity']['ambiguous_open_issues']}", f"- Unresolved date/country records: {report['identity']['unresolved_source_records']}", f"- Discrepancies: {report['discrepancies']['total']}", "", "## Manual review queues", "", f"- AMBIGUOUS: {len(report['manual_review']['ambiguous'])}", f"- DATE CONFLICT: {len(report['manual_review']['date_conflicts'])}", f"- POSSIBLE FALSE MERGE / festival family: {len(report['manual_review']['possible_false_merges'])}", f"- TBC: {len(report['manual_review']['tbc'])}", f"- MULTI-DAY UNRESOLVED: {len(report['manual_review']['multi_day'])}", f"- UNKNOWN COUNTRY: {len(report['manual_review']['unknown_country'])}", "- Full item-level queues are in the JSON report.", "",
        "## Manual sample", "", f"- AIMS-only sampled: {len(report['samples']['aims_only'])}", f"- World Athletics-only sampled: {len(report['samples']['world_athletics_only'])}", f"- Cross-source sampled: {len(report['samples']['cross_source'])}", "",
    ]) + "\n", encoding="utf-8")
    return [json_path, md_path]
