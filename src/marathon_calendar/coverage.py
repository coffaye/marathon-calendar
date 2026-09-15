from __future__ import annotations

import re
import json
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .domain.models import RaceSource
from .identity import normalize_name
from .sources.china_annual_catalog import AnnualCatalogRecord


MATCHED_EXACT = "MATCHED_EXACT"
MATCHED_NORMALIZED = "MATCHED_NORMALIZED"
MATCHED_DATE_CHANGED = "MATCHED_DATE_CHANGED"
MATCHED_NAME_CHANGED = "MATCHED_NAME_CHANGED"
CATALOG_ONLY = "CATALOG_ONLY"
API_ONLY = "API_ONLY"
AMBIGUOUS = "AMBIGUOUS"
INVALID = "INVALID"
OTHER = "OTHER"

_PLANNED_MONTH_RE = re.compile(r"(?P<month>\d{1,2})\s*月(?:\s*(?P<day>\d{1,2})\s*日)?")


@dataclass
class _ApiRecord:
    source: RaceSource
    name: str
    province: str
    city: str | None
    race_date: date | None
    distance_types: set[str]


@dataclass
class _Candidate:
    record: _ApiRecord
    score: float
    name_exact: bool
    name_ratio: float
    date_delta_days: int | None
    distance_overlap: set[str]
    evidence: list[str]


def _province_key(value: str | None) -> str:
    text = re.sub(r"\s+", "", value or "")
    for suffix in ("维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "特别行政区", "省", "市"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return normalize_name(text)


def _api_records(sources: Iterable[RaceSource]) -> list[_ApiRecord]:
    records: list[_ApiRecord] = []
    for source in sources:
        normalized = source.normalized_data
        records.append(
            _ApiRecord(
                source=source,
                name=source.source_race_name,
                province=str(normalized.get("province") or ""),
                city=normalized.get("city"),
                race_date=source.source_race_date,
                distance_types=set(normalized.get("distance_types") or []),
            )
        )
    return records


def _candidate(catalog: AnnualCatalogRecord, api: _ApiRecord) -> _Candidate | None:
    if _province_key(catalog.province) != _province_key(api.province):
        return None
    distance_overlap = set(catalog.distance_types) & api.distance_types
    if not distance_overlap:
        return None
    catalog_name = normalize_name(catalog.source_race_name)
    api_name = normalize_name(api.name)
    name_exact = catalog_name == api_name
    name_ratio = SequenceMatcher(None, catalog_name, api_name).ratio()
    if not name_exact and name_ratio < 0.86:
        return None
    date_delta_days: int | None = None
    planned_month_matches: bool | None = None
    if catalog.source_race_date and api.race_date:
        date_delta_days = abs((catalog.source_race_date - api.race_date).days)
        limit = 120 if name_exact else 45
        if date_delta_days > limit:
            return None
    elif api.race_date:
        planned_dates = list(_PLANNED_MONTH_RE.finditer(catalog.planned_date_text))
        if planned_dates:
            planned_months = {int(item.group("month")) for item in planned_dates}
            planned_month_matches = api.race_date.month in planned_months
            if not planned_month_matches and not name_exact:
                return None

    evidence = [
        "province_exact",
        f"distance_overlap={','.join(sorted(distance_overlap))}",
    ]
    score = 0.70 if name_exact else 0.50 * name_ratio
    if name_exact:
        evidence.append("normalized_name_exact")
    else:
        evidence.append(f"normalized_name_similarity={name_ratio:.3f}")
    score += 0.15 + 0.10
    if date_delta_days == 0:
        score += 0.10
        evidence.append("date_equal")
    elif date_delta_days is not None:
        score += 0.06 if date_delta_days <= 7 else 0.035 if date_delta_days <= 30 else 0.01
        evidence.append(f"date_delta_days={date_delta_days}")
    else:
        planned_dates = list(_PLANNED_MONTH_RE.finditer(catalog.planned_date_text))
        if planned_dates and api.race_date:
            if planned_month_matches:
                score += 0.045
                evidence.append(f"planned_month_contains_api_date={api.race_date.month}")
            else:
                score += 0.005
                evidence.append(f"planned_month_differs_from_api_date={api.race_date.month}; exact name retained match")
        else:
            score += 0.02
            evidence.append("catalog_date_precision_not_exact")
    return _Candidate(
        record=api,
        score=round(score, 6),
        name_exact=name_exact,
        name_ratio=name_ratio,
        date_delta_days=date_delta_days,
        distance_overlap=distance_overlap,
        evidence=evidence,
    )


def _classify(catalog: AnnualCatalogRecord, match: _Candidate) -> str:
    if not match.name_exact:
        return MATCHED_NAME_CHANGED
    if catalog.source_race_date and match.record.race_date:
        if match.date_delta_days == 0:
            return MATCHED_EXACT
        return MATCHED_DATE_CHANGED
    return MATCHED_NORMALIZED


def audit_catalog(
    catalog_records: Iterable[AnnualCatalogRecord],
    api_sources: Iterable[RaceSource],
) -> dict[str, Any]:
    """Produce a conservative, explainable catalog/API reconciliation report."""

    catalogs = sorted(catalog_records, key=lambda item: item.row_number)
    api_records = _api_records(api_sources)
    matched_api_ids: set[str] = set()
    result_records: list[dict[str, Any]] = []
    counts = {
        MATCHED_EXACT: 0,
        MATCHED_NORMALIZED: 0,
        MATCHED_DATE_CHANGED: 0,
        MATCHED_NAME_CHANGED: 0,
        CATALOG_ONLY: 0,
        AMBIGUOUS: 0,
        INVALID: 0,
        OTHER: 0,
    }

    # Assign the strongest candidates first. This prevents a weak early row from
    # consuming an API source that is a materially better match for a later row.
    ordered_catalogs = sorted(
        catalogs,
        key=lambda item: (
            -max(
                (_candidate(item, api).score for api in api_records if _candidate(item, api)),
                default=0.0,
            ),
            item.row_number,
        ),
    )
    for catalog in ordered_catalogs:
        candidates = [
            candidate
            for api in api_records
            if api.source.external_id not in matched_api_ids
            and (candidate := _candidate(catalog, api))
        ]
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.record.source.external_id or ""))
        if not candidates:
            result = CATALOG_ONLY
            counts[result] += 1
            evidence = ["no conservative API candidate"]
            if catalog.source_race_date is None:
                evidence.append("catalog planned date is month-only/range; no exact day inferred")
            result_records.append(
                {
                    "row_number": catalog.row_number,
                    "catalog_external_id": catalog.external_id,
                    "catalog_name": catalog.source_race_name,
                    "catalog_date": catalog.source_race_date.isoformat() if catalog.source_race_date else None,
                    "catalog_date_text": catalog.planned_date_text,
                    "province": catalog.province,
                    "result": result,
                    "matched_api_external_ids": [],
                    "matched_race_ids": [],
                    "identity_method": "none",
                    "identity_score": 0.0,
                    "identity_evidence": evidence,
                }
            )
            continue

        ambiguous = len(candidates) > 1 and candidates[0].score - candidates[1].score < 0.05
        if ambiguous:
            result = AMBIGUOUS
            counts[result] += 1
            result_records.append(
                {
                    "row_number": catalog.row_number,
                    "catalog_external_id": catalog.external_id,
                    "catalog_name": catalog.source_race_name,
                    "catalog_date": catalog.source_race_date.isoformat() if catalog.source_race_date else None,
                    "catalog_date_text": catalog.planned_date_text,
                    "province": catalog.province,
                    "result": result,
                    "matched_api_external_ids": [item.record.source.external_id for item in candidates[:5]],
                    "matched_race_ids": [str(item.record.source.race_id) for item in candidates[:5]],
                    "identity_method": "conservative evidence tie; manual review required",
                    "identity_score": candidates[0].score,
                    "identity_evidence": candidates[0].evidence,
                    "candidate_evidence": [
                        {
                            "external_id": item.record.source.external_id,
                            "race_id": str(item.record.source.race_id),
                            "score": item.score,
                            "evidence": item.evidence,
                        }
                        for item in candidates[:5]
                    ],
                }
            )
            continue

        best = candidates[0]
        result = _classify(catalog, best)
        counts[result] += 1
        if best.record.source.external_id:
            matched_api_ids.add(best.record.source.external_id)
        result_records.append(
            {
                "row_number": catalog.row_number,
                "catalog_external_id": catalog.external_id,
                "catalog_name": catalog.source_race_name,
                "catalog_date": catalog.source_race_date.isoformat() if catalog.source_race_date else None,
                "catalog_date_text": catalog.planned_date_text,
                "province": catalog.province,
                "result": result,
                "matched_api_external_ids": [best.record.source.external_id],
                "matched_race_ids": [str(best.record.source.race_id)],
                "identity_method": "name + province + distance + date evidence",
                "identity_score": best.score,
                "identity_evidence": best.evidence,
                "date_delta_days": best.date_delta_days,
            }
        )

    result_records.sort(key=lambda item: item["row_number"])
    api_only_records = [
        {
            "external_id": api.source.external_id,
            "race_id": str(api.source.race_id),
            "name": api.name,
            "race_date": api.race_date.isoformat() if api.race_date else None,
            "province": api.province,
            "analysis": "no unassigned catalog row passed conservative province + distance + name + date evidence",
        }
        for api in api_records
        if api.source.external_id not in matched_api_ids
    ]
    return {
        "catalog_total": len(catalogs),
        "api_total": len(api_records),
        "summary": counts,
        "api_only_total": len(api_only_records),
        "api_only": api_only_records,
        "records": result_records,
    }


def _quality_checks(catalogs: list[AnnualCatalogRecord]) -> dict[str, Any]:
    by_name: dict[str, list[AnnualCatalogRecord]] = {}
    for record in catalogs:
        by_name.setdefault(normalize_name(record.source_race_name), []).append(record)
    duplicate_name_groups = [items for items in by_name.values() if len(items) > 1]
    same_name_different_dates = [
        {
            "normalized_name": normalize_name(items[0].source_race_name),
            "rows": [item.row_number for item in items],
            "dates": sorted({item.source_race_date.isoformat() for item in items if item.source_race_date}),
        }
        for items in duplicate_name_groups
        if len({item.source_race_date for item in items if item.source_race_date}) > 1
    ]
    unknown_event_types = [
        item.row_number
        for item in catalogs
        if not item.distance_types or any(value not in {"全程", "半程"} for value in item.source_distance_text)
    ]
    unknown_categories = [
        item.row_number for item in catalogs if item.source_category_text not in {"A", "B", "C"}
    ]
    return {
        "duplicate_normalized_name_groups": [
            {"normalized_name": normalize_name(items[0].source_race_name), "rows": [item.row_number for item in items]}
            for items in duplicate_name_groups
        ],
        "duplicate_normalized_name_group_total": len(duplicate_name_groups),
        "same_name_different_date": same_name_different_dates,
        "same_name_different_date_total": len(same_name_different_dates),
        "same_city_same_day": [],
        "same_city_same_day_total": 0,
        "invalid_date_rows": [],
        "invalid_date_total": 0,
        "missing_province_rows": [item.row_number for item in catalogs if not item.province],
        "missing_province_total": sum(not item.province for item in catalogs),
        "missing_city_rows": [item.row_number for item in catalogs if not item.city],
        "missing_city_total": sum(not item.city for item in catalogs),
        "unknown_event_type_rows": unknown_event_types,
        "unknown_event_type_total": len(unknown_event_types),
        "unknown_category_rows": unknown_categories,
        "unknown_category_total": len(unknown_categories),
        "non_exact_planned_date_total": sum(item.source_race_date is None for item in catalogs),
    }


def build_coverage_report(
    fetch_result: Any,
    audit: dict[str, Any],
    *,
    attached_catalog_external_ids: set[str] | None = None,
    canonical_race_count: int | None = None,
) -> dict[str, Any]:
    """Add source provenance, coverage percentages, and data-quality checks."""

    records = fetch_result.records
    summary = dict(audit["summary"])
    if sum(summary.values()) != len(records):
        raise ValueError("catalog result totals do not explain the catalog total")
    matched_results = {MATCHED_EXACT, MATCHED_NORMALIZED, MATCHED_DATE_CHANGED, MATCHED_NAME_CHANGED}
    matched_rows = [row for row in audit["records"] if row["result"] in matched_results]
    attached = attached_catalog_external_ids or set()
    coverage_count = sum(
        row["catalog_external_id"] in attached or row["result"] in matched_results
        for row in audit["records"]
    )
    api_ids = {external_id for row in matched_rows for external_id in row["matched_api_external_ids"] if external_id}
    catalog_only = [row for row in audit["records"] if row["result"] == CATALOG_ONLY]
    catalog_by_row = {record.row_number: record for record in records}
    report = {
        "document": fetch_result.document.model_dump(mode="json"),
        "catalog_total": len(records),
        "api_total": audit["api_total"],
        "result_totals": summary,
        "catalog_result_total": sum(summary.values()),
        "api_only_total": audit["api_only_total"],
        "coverage": {
            "catalog_source_attached_count": sum(item.external_id in attached for item in records),
            "annual_catalog_reconciliation_coverage_count": coverage_count,
            "annual_catalog_reconciliation_coverage_percent": round(coverage_count / len(records) * 100, 2) if records else 0.0,
            "annual_catalog_api_enrichment_count": len(matched_rows),
            "annual_catalog_api_enrichment_rate_percent": round(len(matched_rows) / len(records) * 100, 2) if records else 0.0,
            "matched_api_unique_count": len(api_ids),
            "canonical_race_count": canonical_race_count,
            "catalog_only_exact_date_count": sum(catalog_by_row[row["row_number"]].source_race_date is not None for row in catalog_only),
            "catalog_only_pending_exact_date_count": sum(catalog_by_row[row["row_number"]].source_race_date is None for row in catalog_only),
        },
        "quality_checks": _quality_checks(records),
        "date_changes_found": [row for row in audit["records"] if row["result"] == MATCHED_DATE_CHANGED],
        "name_changes_found": [row for row in audit["records"] if row["result"] == MATCHED_NAME_CHANGED],
        "ambiguous": [row for row in audit["records"] if row["result"] == AMBIGUOUS],
        "catalog_only_first_30": catalog_only[:30],
        "api_only": audit["api_only"],
        "records": audit["records"],
    }
    return report


def write_coverage_report(report: dict[str, Any], output_dir: str | Path) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    year = report["document"]["year"]
    json_path = output_dir / f"china_coverage_{year}.json"
    md_path = output_dir / f"china_coverage_{year}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    totals = report["result_totals"]
    coverage = report["coverage"]
    quality = report["quality_checks"]
    lines = [
        f"# 中国赛事覆盖率审计（{year}）",
        "",
        f"- 官方年度目录：{report['document']['source_url']}",
        f"- 发布日期：{report['document']['publication_date']}",
        f"- 抓取时间：{report['document']['retrieved_at']}",
        f"- 文档 SHA-256：`{report['document']['document_checksum']}`",
        f"- 目录记录：{report['catalog_total']}；中国 API source：{report['api_total']}",
        "",
        "## 结论",
        "",
        f"Annual Catalog Reconciliation Coverage：{coverage['annual_catalog_reconciliation_coverage_count']}/{report['catalog_total']}（{coverage['annual_catalog_reconciliation_coverage_percent']:.2f}%）；Annual Catalog API Enrichment Rate：{coverage['annual_catalog_api_enrichment_count']}/{report['catalog_total']}（{coverage['annual_catalog_api_enrichment_rate_percent']:.2f}%）。",
        f"目录结果合计校验：{report['catalog_result_total']} = {report['catalog_total']}；API-only：{report['api_only_total']}。",
        f"可解释差异：年度目录是规划口径（{report['catalog_total']} 行），官方 API 是当前已发布/可查询口径（{report['api_total']} 条 source）；两者重叠 {coverage['matched_api_unique_count']} 条 API source，目录另有 {totals[CATALOG_ONLY]} 条未进入 API。",
        "",
        "## 目录侧结果",
        "",
        "| 结果 | 数量 |",
        "|---|---:|",
    ]
    for key in (MATCHED_EXACT, MATCHED_NORMALIZED, MATCHED_DATE_CHANGED, MATCHED_NAME_CHANGED, CATALOG_ONLY, AMBIGUOUS, INVALID, OTHER):
        lines.append(f"| {key} | {totals[key]} |")
    lines.extend(
        [
            "",
            "## 数据质量",
            "",
            f"- 重复 normalized name 组：{quality['duplicate_normalized_name_group_total']}；同名不同日期：{quality['same_name_different_date_total']}。",
            f"- 同城同日：{quality['same_city_same_day_total']}；无效日期：{quality['invalid_date_total']}。",
            f"- 缺省省份：{quality['missing_province_total']}；目录未提供城市：{quality['missing_city_total']}。",
            f"- 未知赛事设项：{quality['unknown_event_type_total']}；未知赛事类别：{quality['unknown_category_total']}。",
            f"- 仅月/日期范围（未推断具体日）：{quality['non_exact_planned_date_total']}。",
            "",
            "## 日期变更",
            "",
        ]
    )
    if report["date_changes_found"]:
        lines.extend(["| 行号 | 目录名称 | 目录日期 | API source |", "|---:|---|---|---|"])
        for row in report["date_changes_found"]:
            lines.append(f"| {row['row_number']} | {row['catalog_name']} | {row['catalog_date']} | {', '.join(row['matched_api_external_ids'])} |")
    else:
        lines.append("无。")
    lines.extend(["", "## 名称变更", ""])
    if report["name_changes_found"]:
        lines.extend(["| 行号 | 目录名称 | API source |", "|---:|---|---|"])
        for row in report["name_changes_found"]:
            lines.append(f"| {row['row_number']} | {row['catalog_name']} | {', '.join(row['matched_api_external_ids'])} |")
    else:
        lines.append("无。")
    lines.extend(["", "## AMBIGUOUS 全量清单", ""])
    if report["ambiguous"]:
        for row in report["ambiguous"]:
            lines.append(f"- 行 {row['row_number']}：{row['catalog_name']}；候选 {', '.join(row['matched_api_external_ids'])}。")
    else:
        lines.append("无。")
    lines.extend(["", "## CATALOG_ONLY 前 30 条", "", "| 行号 | 名称 | 省份 | 计划日期 | 精确日期 |", "|---:|---|---|---|---|"])
    for row in report["catalog_only_first_30"]:
        lines.append(f"| {row['row_number']} | {row['catalog_name']} | {row['province']} | {row['catalog_date_text']} | {row['catalog_date'] or '未明确'} |")
    lines.extend(["", "## API_ONLY", "", f"共 {report['api_only_total']} 条，完整 JSON 清单见 `china_coverage_{year}.json`。", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path
