import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from marathon_calendar.sources.china_annual_catalog import (
    AnnualCatalogRecord,
    CatalogDocument,
    CatalogSchemaError,
    ChinaAnnualCatalogSource,
    parse_extracted_text,
)


REAL_CATALOG_PDF = Path(__file__).parents[1] / "data" / "source_documents" / "china_annual_catalog_2026.pdf"
REAL_CATALOG_SHA256 = "52633a1251d85324eb269872fdcfca3e3682aa8902f7c9565fb08384ec05128a"


def _sidecar_payload(*, checksum: str = "a" * 64, count: int = 492) -> dict:
    document = CatalogDocument(
        title="fixture catalog",
        year=2026,
        source_url="https://example.com/catalog.pdf",
        publication_date=date(2025, 12, 19),
        retrieved_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
        document_checksum=checksum,
        extraction_method="test sidecar",
    )
    records = [
        AnnualCatalogRecord(
            row_number=row_number,
            source_page=1,
            year=2026,
            source_race_name=f"测试赛事 {row_number}",
            source_race_date=date(2026, 1, 1),
            planned_date_text="1月1日",
            province="北京",
            organization="测试单位",
            distance_types=["marathon"],
            source_distance_text=["全程"],
            association_level="A",
            source_category_text="A",
            external_id=f"catalog:2026:fixture-{row_number}",
            source_document="https://example.com/catalog.pdf",
            source_document_checksum=checksum,
            source_publication_date=date(2025, 12, 19),
            raw_data={"row_number": row_number},
        ).model_dump(mode="json")
        for row_number in range(1, count + 1)
    ]
    return {"document": document.model_dump(mode="json"), "records": records}


def _write_sidecar(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.mark.skipif(
    not REAL_CATALOG_PDF.exists(),
    reason="official 2026 catalog PDF is not checked into the CI repository",
)
def test_real_2026_catalog_document_is_492_rows_and_checksum_validated():
    result = ChinaAnnualCatalogSource(REAL_CATALOG_PDF).fetch(year=2026)
    assert len(result.records) == 492
    assert [record.row_number for record in result.records] == list(range(1, 493))
    assert result.document.document_checksum == REAL_CATALOG_SHA256
    assert sum(record.association_level == "A" for record in result.records) == 254
    assert sum(record.association_level == "B" for record in result.records) == 67
    assert sum(record.association_level == "C" for record in result.records) == 171
    assert len({record.external_id for record in result.records}) == 492


def test_catalog_source_accepts_valid_sidecar_fixture_without_pdf(tmp_path):
    sidecar = tmp_path / "catalog.json"
    _write_sidecar(sidecar, _sidecar_payload())

    result = ChinaAnnualCatalogSource(sidecar).fetch(year=2026)

    assert len(result.records) == 492
    assert result.records[0].source_race_name == "测试赛事 1"
    assert result.records[-1].row_number == 492
    assert result.records[0].distance_types == ["marathon"]


def test_catalog_source_rejects_invalid_sidecar_schema(tmp_path):
    sidecar = tmp_path / "catalog.json"
    payload = _sidecar_payload()
    payload["document"].pop("year")
    _write_sidecar(sidecar, payload)

    with pytest.raises(CatalogSchemaError, match="invalid annual catalog sidecar"):
        ChinaAnnualCatalogSource(sidecar).fetch(year=2026)


def test_catalog_source_rejects_pdf_checksum_mismatch_without_using_real_pdf(tmp_path):
    pdf = tmp_path / "catalog.pdf"
    pdf.write_bytes(b"synthetic PDF bytes")
    _write_sidecar(pdf.with_suffix(".json"), _sidecar_payload())

    actual_checksum = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert actual_checksum != "a" * 64
    with pytest.raises(CatalogSchemaError, match="checksum mismatch"):
        ChinaAnnualCatalogSource(pdf).fetch(year=2026)


def test_catalog_text_parser_normalizes_fixture_rows_and_validates_full_shape():
    text = "\n".join(
        f"{row_number} 北京 测试赛事 {row_number} 1月1日 测试单位 全程 A"
        for row_number in range(1, 493)
    )

    result = parse_extracted_text(
        text,
        year=2026,
        retrieved_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
        document_checksum="b" * 64,
    )

    assert len(result.records) == 492
    assert result.records[0].source_race_date == date(2026, 1, 1)
    assert result.records[0].association_level == "A"
    assert result.records[0].distance_types == ["marathon"]
    assert len({record.external_id for record in result.records}) == 492
