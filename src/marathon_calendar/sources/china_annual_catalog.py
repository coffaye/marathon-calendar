from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from ..domain.models import SourceRole
from ..identity import canonical_identity_key


SOURCE_NAME = "china_annual_catalog"
SOURCE_ROLE = SourceRole.planning_catalog
PUBLISHABLE = False
DOCUMENT_URL = (
    "https://file.shuzixindong.com/changzheng/84554/"
    "fddbe29f6e434035918201d2a17dbcac.pdf"
)
PUBLICATION_DATE = date(2025, 12, 19)

_PROVINCES = (
    "北京",
    "天津",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
)
_ROW_RE = re.compile(r"^(\d{1,3})\s+(.+)$")
_LINE_RE = re.compile(r"^L(\d+)@P(\d+)[^:]*:\s?(.*)$")
_DATE_RE = re.compile(
    r"(待定|\d{1,2}\s*月\s*(?:\d{1,2}\s*日)?"
    r"(?:\s*[—\-~至]\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?)"
)
_ITEM_RE = re.compile(r"(全程/半程|全程|半程)\s*([ABC])(?=\s|$|[－—-])")


class CatalogSchemaError(ValueError):
    pass


class AnnualCatalogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(ge=1)
    source_page: int | None = Field(default=None, ge=1)
    year: int = Field(ge=1900, le=2200)
    source_race_name: str = Field(min_length=1)
    source_race_date: date | None = None
    planned_date_text: str = Field(min_length=1)
    province: str = Field(min_length=1)
    city: str | None = None
    organization: str | None = None
    distance_types: list[str] = Field(default_factory=list)
    source_distance_text: list[str] = Field(default_factory=list)
    association_level: str | None = None
    source_category_text: str | None = None
    external_id: str = Field(min_length=1)
    source_document: HttpUrl
    source_document_checksum: str = Field(min_length=64, max_length=64)
    source_publication_date: date
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @property
    def identity_key(self) -> str:
        return canonical_identity_key(
            name=self.source_race_name,
            country="CHN",
            city=self.city,
        )


class CatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    year: int = Field(ge=1900, le=2200)
    source_url: HttpUrl
    publication_date: date
    retrieved_at: datetime
    document_checksum: str = Field(min_length=64, max_length=64)
    extraction_method: str


@dataclass
class CatalogFetchResult:
    document: CatalogDocument
    records: list[AnnualCatalogRecord]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_exact_date(value: str, year: int) -> date | None:
    compact = re.sub(r"\s+", "", value)
    match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", compact)
    if not match:
        return None
    try:
        return date(year, int(match.group(1)), int(match.group(2)))
    except ValueError as exc:
        raise CatalogSchemaError(f"invalid catalog date: {value!r}") from exc


def _source_external_id(
    *, year: int, name: str, province: str, planned_date_text: str, organizer: str | None, item: str, category: str
) -> str:
    identity_material = {
        "year": year,
        "name": _clean(name),
        "province": _clean(province),
        "planned_date_text": _clean(planned_date_text),
        "organization": _clean(organizer or ""),
        "event_types": _clean(item),
        "category": _clean(category),
    }
    digest = hashlib.sha256(
        json.dumps(identity_material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"catalog:{year}:{digest}"


def parse_extracted_text(
    text: str,
    *,
    year: int,
    source_url: str = DOCUMENT_URL,
    publication_date: date = PUBLICATION_DATE,
    retrieved_at: datetime,
    document_checksum: str,
    extraction_method: str = "official PDF table text extraction",
) -> CatalogFetchResult:
    """Parse the line-oriented text extracted from the official PDF table.

    The PDF's embedded font has no reliable Unicode mapping in some local PDF
    libraries. The parser therefore consumes a line extraction/OCR result and
    keeps the original row text in ``raw_data``. It does not infer an exact day
    from a month-only or date-range entry.
    """

    lines: list[tuple[int | None, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        match = _LINE_RE.match(line)
        if match:
            lines.append((int(match.group(2)) + 1, match.group(3).strip()))
        elif line.strip():
            lines.append((None, line.strip()))

    starts: list[tuple[int, int, str, int | None]] = []
    for index, (page, line) in enumerate(lines):
        match = _ROW_RE.match(line)
        if not match:
            continue
        row_number = int(match.group(1))
        province = match.group(2).split()[0]
        if 1 <= row_number <= 492 and province in _PROVINCES:
            starts.append((index, row_number, match.group(2), page))
    if not starts:
        raise CatalogSchemaError("no annual catalog rows found in extracted text")

    records: list[AnnualCatalogRecord] = []
    for position, (start, row_number, first_rest, page) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        segment = lines[start:end]
        province = first_rest.split()[0]
        consumed = 1
        if province == "新疆" and len(segment) > 1 and segment[1][1] == "兵团":
            province = "新疆兵团"
            consumed = 2
        first_name_part = first_rest[len(first_rest.split()[0]) :].strip()
        body_parts = []
        if first_name_part and province != "新疆兵团":
            body_parts.append(first_name_part)
        body_parts.extend(line for _, line in segment[consumed:])
        body = " ".join(body_parts)
        date_match = _DATE_RE.search(body)
        item_matches = list(_ITEM_RE.finditer(body))
        if date_match is None or not item_matches:
            raise CatalogSchemaError(f"catalog row {row_number} has incomplete date/item fields")
        item_match = item_matches[-1]
        planned_date_text = _clean(date_match.group(1))
        source_race_name = _clean(body[: date_match.start()])
        source_distance_text = [part for part in item_match.group(1).split("/") if part]
        distance_types = [
            {"全程": "marathon", "半程": "half_marathon"}[part]
            for part in source_distance_text
            if part in {"全程", "半程"}
        ]
        category = item_match.group(2)
        organization = _clean(body[date_match.end() : item_match.start()]) or None
        records.append(
            AnnualCatalogRecord(
                row_number=row_number,
                source_page=page,
                year=year,
                source_race_name=source_race_name,
                source_race_date=_parse_exact_date(planned_date_text, year),
                planned_date_text=planned_date_text,
                province=province,
                organization=organization,
                distance_types=distance_types,
                source_distance_text=source_distance_text,
                association_level=category,
                source_category_text=category,
                external_id=_source_external_id(
                    year=year,
                    name=source_race_name,
                    province=province,
                    planned_date_text=planned_date_text,
                    organizer=organization,
                    item=item_match.group(1),
                    category=category,
                ),
                source_document=source_url,
                source_document_checksum=document_checksum.lower(),
                source_publication_date=publication_date,
                raw_data={
                    "row_number": row_number,
                    "source_page": page,
                    "province": province,
                    "race_name": source_race_name,
                    "planned_date": planned_date_text,
                    "organizer": organization,
                    "event_types": item_match.group(1),
                    "category": category,
                },
            )
        )

    if len(records) != 492 or [record.row_number for record in records] != list(range(1, 493)):
        raise CatalogSchemaError("annual catalog must contain exactly the sequential rows 1..492")
    document = CatalogDocument(
        title="2026 年全国马拉松赛事目录",
        year=year,
        source_url=source_url,
        publication_date=publication_date,
        retrieved_at=retrieved_at,
        document_checksum=document_checksum.lower(),
        extraction_method=extraction_method,
    )
    return CatalogFetchResult(document=document, records=records)


class ChinaAnnualCatalogSource:
    """File-backed adapter for the CAA annual planning catalog publication."""

    def __init__(self, document_path: str | Path):
        self.document_path = Path(document_path)

    def fetch(self, *, year: int | None = None) -> CatalogFetchResult:
        sidecar_path = self.document_path
        if self.document_path.suffix.casefold() == ".pdf":
            sidecar_path = self.document_path.with_suffix(".json")
            if not self.document_path.exists():
                raise CatalogSchemaError(f"official annual catalog document not found: {self.document_path}")
            actual_checksum = hashlib.sha256(self.document_path.read_bytes()).hexdigest()
        elif self.document_path.suffix.casefold() == ".json":
            actual_checksum = None
            sibling_pdf = self.document_path.with_suffix(".pdf")
            if sibling_pdf.exists():
                actual_checksum = hashlib.sha256(sibling_pdf.read_bytes()).hexdigest()
        else:
            raise CatalogSchemaError("annual catalog adapter accepts the official PDF or its normalized JSON sidecar")
        try:
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            document = CatalogDocument.model_validate(payload["document"])
            records = [AnnualCatalogRecord.model_validate(item) for item in payload["records"]]
        except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CatalogSchemaError(f"invalid annual catalog sidecar: {sidecar_path}") from exc
        if actual_checksum and document.document_checksum.lower() != actual_checksum.lower():
            raise CatalogSchemaError(
                f"annual catalog PDF checksum mismatch: expected {document.document_checksum}, got {actual_checksum}"
            )
        if year is not None and document.year != year:
            raise CatalogSchemaError(f"catalog year {document.year} does not match requested year {year}")
        if len(records) != 492 or {record.row_number for record in records} != set(range(1, 493)):
            raise CatalogSchemaError("annual catalog sidecar must contain exactly 492 unique rows")
        return CatalogFetchResult(document=document, records=sorted(records, key=lambda item: item.row_number))
