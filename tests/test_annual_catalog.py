from pathlib import Path

from marathon_calendar.sources.china_annual_catalog import ChinaAnnualCatalogSource


def test_real_2026_catalog_document_is_492_rows_and_checksum_validated():
    document = Path(__file__).parents[1] / "data" / "source_documents" / "china_annual_catalog_2026.pdf"
    result = ChinaAnnualCatalogSource(document).fetch(year=2026)
    assert len(result.records) == 492
    assert [record.row_number for record in result.records] == list(range(1, 493))
    assert result.document.document_checksum == "52633a1251d85324eb269872fdcfca3e3682aa8902f7c9565fb08384ec05128a"
    assert sum(record.association_level == "A" for record in result.records) == 254
    assert sum(record.association_level == "B" for record in result.records) == 67
    assert sum(record.association_level == "C" for record in result.records) == 171
    assert len({record.external_id for record in result.records}) == 492
