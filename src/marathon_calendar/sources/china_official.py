from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from ..domain.models import RaceStatus, SourceRole
from ..identity import canonical_identity_key
from .http_client import HttpClientError, JsonHttpClient


LIST_ENDPOINT = (
    "https://api-changzheng.chinaath.com/changzheng-content-center-api/"
    "api/homePage/official/searchCompetitionMls"
)
DETAIL_ENDPOINT = (
    "https://api-changzheng.chinaath.com/changzheng-content-center-api/"
    "api/homePage/official/searchById"
)
OFFICIAL_DETAIL_PAGE = "https://www.runchina.org.cn/race/v/detail/{external_id}"
SOURCE_NAME = "china_official"
SOURCE_ROLE = SourceRole.live_official
PUBLISHABLE = True


class SourceSchemaError(ValueError):
    pass


class NormalizedChinaRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_id: str
    source_race_name: str
    source_race_date: date
    country: str = "CHN"
    province: str | None = None
    city: str | None = None
    distance_types: list[str] = Field(default_factory=list)
    source_distance_text: list[str] = Field(default_factory=list)
    association_level: str | None = None
    source_category_text: str | None = None
    organization: str | None = None
    world_athletics_label: str | None = None
    status: RaceStatus | None = None
    official_url: str | None = None
    list_source_hash: str
    source_hash: str
    detail_fetched: bool = False
    list_raw: dict[str, Any]
    detail_raw: dict[str, Any] | None = None

    @property
    def identity_key(self) -> str:
        return canonical_identity_key(
            name=self.source_race_name,
            country=self.country,
            city=self.city,
        )


@dataclass
class ListFetchResult:
    pages: list[dict[str, Any]]
    all_records: list[dict[str, Any]]
    records: list[dict[str, Any]]
    page_count: int
    total_count: int
    request_payload: dict[str, Any]


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_date(value: Any) -> date:
    if not isinstance(value, str) or not value.strip():
        raise SourceSchemaError("race date is missing")
    text = value.strip().replace(".", "-").replace("/", "-")
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise SourceSchemaError(f"invalid race date: {value!r}") from exc


def _split_address(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, None
    parts = [part.strip() for part in value.split("/") if part.strip()]
    return (parts[0] if parts else None, parts[1] if len(parts) > 1 else None)


DISTANCE_MAP = {
    "全程": "marathon",
    "马拉松": "marathon",
    "全程马拉松": "marathon",
    "42.195公里": "marathon",
    "半程": "half_marathon",
    "半马": "half_marathon",
    "半程马拉松": "half_marathon",
    "21.0975公里": "half_marathon",
    "10公里": "10k",
    "10km": "10k",
    "10k": "10k",
    "5公里": "5k",
    "5km": "5k",
    "迷你跑": "5k",
    "欢乐跑": "fun_run",
    "健康跑": "fun_run",
    "越野": "trail",
    "超马": "ultra",
}


def normalize_distances(value: Any) -> tuple[list[str], list[str]]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
    elif isinstance(value, list):
        parsed = value
    else:
        parsed = []
    source_text = [str(item).strip() for item in parsed if str(item).strip()]
    normalized: list[str] = []
    for item in source_text:
        key = item.casefold()
        mapped = next((result for label, result in DISTANCE_MAP.items() if label.casefold() == key), None)
        if mapped and mapped not in normalized:
            normalized.append(mapped)
    return normalized, source_text


def normalize_category(value: Any) -> tuple[str | None, str | None]:
    source_text = str(value or "").strip() or None
    if not source_text:
        return None, None
    if source_text in {"A", "B", "C", "TEN"}:
        return source_text, source_text
    if source_text.startswith("C"):
        return "C", source_text
    if "中国田径协会主办" in source_text or "主办系列赛" in source_text:
        return "TEN", source_text
    return None, source_text


def _status_from_raw(raw: dict[str, Any]) -> RaceStatus | None:
    explicit = str(raw.get("status", "")).strip().casefold()
    mapping = {
        "scheduled": RaceStatus.scheduled,
        "registration_open": RaceStatus.registration_open,
        "registration_closed": RaceStatus.registration_closed,
        "postponed": RaceStatus.postponed,
        "cancelled": RaceStatus.cancelled,
        "canceled": RaceStatus.cancelled,
        "completed": RaceStatus.completed,
        "date_tentative": RaceStatus.date_tentative,
        "取消": RaceStatus.cancelled,
        "延期": RaceStatus.postponed,
    }
    if explicit in mapping:
        return mapping[explicit]
    if raw.get("delay") not in (None, False, "", 0, "0"):
        return RaceStatus.postponed
    return None


class ChinaOfficialSource:
    """Adapter for the China Athletics Association's public calendar frontend API."""

    def __init__(self, client: JsonHttpClient | None = None):
        self.client = client or JsonHttpClient()

    @staticmethod
    def _payload(page_no: int, page_size: int, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        values = {
            "provinceId": "",
            "cityId": "",
            "districtId": "",
            "raceName": "",
            "raceGrade": "",
            "raceStartTime": "",
        }
        if filters:
            values.update({key: value for key, value in filters.items() if value is not None})
        values.update({"pageNo": page_no, "pageSize": page_size})
        return values

    @staticmethod
    def _validate_envelope(payload: dict[str, Any], endpoint: str) -> dict[str, Any]:
        if payload.get("success") is not True:
            raise SourceSchemaError(
                f"business error from {endpoint}: code={payload.get('code')!r} msg={payload.get('msg')!r}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise SourceSchemaError(f"invalid {endpoint} envelope: data must be object")
        return data

    def fetch_race_list(
        self,
        *,
        year: int | None = None,
        page_size: int = 100,
        filters: dict[str, Any] | None = None,
        max_pages: int | None = None,
    ) -> ListFetchResult:
        if page_size < 1:
            raise ValueError("page_size must be >= 1")
        pages: list[dict[str, Any]] = []
        all_records: list[dict[str, Any]] = []
        page = 1
        page_count = 1
        request_payload = self._payload(page, page_size, filters)
        while page <= page_count and (max_pages is None or page <= max_pages):
            payload = self._payload(page, page_size, filters)
            raw = self.client.post_json(LIST_ENDPOINT, payload)
            data = self._validate_envelope(raw, LIST_ENDPOINT)
            results = data.get("results")
            if not isinstance(results, list):
                raise SourceSchemaError("list response data.results must be an array")
            if any(not isinstance(item, dict) for item in results):
                raise SourceSchemaError("list response data.results contains a non-object record")
            pages.append(raw)
            all_records.extend(results)
            page_count = int(data.get("pageCount") or page)
            if page_count < page:
                raise SourceSchemaError("list response pageCount moved backwards")
            page += 1
        if max_pages is not None and page <= page_count:
            raise SourceSchemaError(f"pagination truncated at max_pages={max_pages}")
        if year is None:
            records = all_records
        else:
            records = []
            for item in all_records:
                try:
                    in_year = _parse_date(item.get("raceTime")).year == year
                except SourceSchemaError:
                    in_year = True
                if in_year:
                    records.append(item)
        total_count = int(self._validate_envelope(pages[0], LIST_ENDPOINT).get("totalCount") or len(all_records))
        return ListFetchResult(
            pages=pages,
            all_records=all_records,
            records=records,
            page_count=page_count,
            total_count=total_count,
            request_payload=request_payload,
        )

    def fetch_race_detail(self, external_id: str) -> dict[str, Any]:
        payload = {"id": external_id, "pageTitleLevelTwo": "", "type": "SS"}
        raw = self.client.post_json(DETAIL_ENDPOINT, payload)
        data = self._validate_envelope(raw, DETAIL_ENDPOINT)
        if not isinstance(data.get("ssdetails"), dict):
            raise SourceSchemaError("detail response data.ssdetails must be an object")
        return raw

    def normalize_list_item(self, raw: dict[str, Any]) -> NormalizedChinaRecord:
        external_id = str(raw.get("raceId", "")).strip()
        if not external_id:
            raise SourceSchemaError("list record raceId is missing")
        name = str(raw.get("raceName", "")).strip()
        if not name:
            raise SourceSchemaError(f"list record {external_id} raceName is missing")
        race_date = _parse_date(raw.get("raceTime"))
        province, city = _split_address(raw.get("raceAddress"))
        distance_types, source_distance_text = normalize_distances(raw.get("raceItem"))
        association_level, source_category_text = normalize_category(raw.get("raceGrade"))
        list_hash = canonical_json_hash(raw)
        return NormalizedChinaRecord(
            external_id=external_id,
            source_race_name=name,
            source_race_date=race_date,
            province=province,
            city=city,
            distance_types=distance_types,
            source_distance_text=source_distance_text,
            association_level=association_level,
            source_category_text=source_category_text,
            official_url=OFFICIAL_DETAIL_PAGE.format(external_id=external_id),
            status=_status_from_raw(raw),
            list_source_hash=list_hash,
            source_hash=list_hash,
            list_raw=raw,
        )

    def normalize_detail(
        self, detail_raw: dict[str, Any], base: NormalizedChinaRecord
    ) -> NormalizedChinaRecord:
        data = self._validate_envelope(detail_raw, DETAIL_ENDPOINT)
        details = data.get("ssdetails")
        if not isinstance(details, dict):
            raise SourceSchemaError("detail response data.ssdetails must be an object")
        name = str(details.get("name") or base.source_race_name).strip()
        date_value = details.get("gameDate") or base.source_race_date.isoformat()
        race_date = _parse_date(date_value)
        province = str(details.get("province") or base.province or "").strip() or None
        city = str(details.get("city") or base.city or "").strip() or None
        distance_types, source_distance_text = normalize_distances(details.get("project"))
        if not distance_types:
            distance_types = base.distance_types
            source_distance_text = base.source_distance_text
        association_level, source_category_text = normalize_category(details.get("raceGrade"))
        if association_level is None:
            association_level = base.association_level
        if source_category_text is None:
            source_category_text = base.source_category_text
        detail_payload = details
        raw_combined = {"list": base.list_raw, "detail": detail_raw}
        return base.model_copy(
            update={
                "source_race_name": name,
                "source_race_date": race_date,
                "province": province,
                "city": city,
                "distance_types": distance_types,
                "source_distance_text": source_distance_text,
                "association_level": association_level,
                "source_category_text": source_category_text,
                "organization": str(details.get("compNameOrganizer") or "").strip() or None,
                "world_athletics_label": (
                    "elite_label" if details.get("worldAthleticsGradeLogoUrl") else None
                ),
                "status": _status_from_raw({**base.list_raw, **detail_payload}),
                "detail_fetched": True,
                "detail_raw": detail_raw,
                "source_hash": canonical_json_hash(raw_combined),
                "official_url": str(details.get("webUrl") or base.official_url),
            }
        )
