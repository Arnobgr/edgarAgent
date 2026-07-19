"""Multi-filing corpus fetch — cache-hit paths only (no network)."""

from pathlib import Path

from pydantic import SecretStr

from fie.config import Settings
from fie.tools.edgar import FilingDocument, fetch_recent_filings


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, anthropic_api_key=SecretStr("x"), data_dir=tmp_path)


def _doc(form_type: str, accession: str) -> FilingDocument:
    return FilingDocument(
        doc_id=f"AAPL-{form_type}-{accession}",
        ticker="AAPL",
        form_type=form_type,
        accession_no=accession,
        filing_date="2026-05-01",
        sections={"Item 1": "Business description, long enough to matter."},
    )


def test_fetch_recent_filings_serves_annual_plus_quarterlies_from_cache(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    filings_dir = tmp_path / "filings"
    filings_dir.mkdir()
    (filings_dir / "AAPL_10-K.json").write_text(_doc("10-K", "acc-k").model_dump_json())
    quarterlies = [_doc("10-Q", f"acc-q{i}") for i in range(1, 3)]
    (filings_dir / "AAPL_10-Q_last2.json").write_text(
        '{"documents": [' + ",".join(d.model_dump_json() for d in quarterlies) + "]}"
    )

    docs = fetch_recent_filings("AAPL", quarters=2, settings=settings)

    assert [d.accession_no for d in docs] == ["acc-k", "acc-q1", "acc-q2"]
    assert [d.form_type for d in docs] == ["10-K", "10-Q", "10-Q"]


def test_fetch_recent_filings_zero_quarters_is_annual_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    filings_dir = tmp_path / "filings"
    filings_dir.mkdir()
    (filings_dir / "AAPL_10-K.json").write_text(_doc("10-K", "acc-k").model_dump_json())

    docs = fetch_recent_filings("AAPL", quarters=0, settings=settings)

    assert [d.accession_no for d in docs] == ["acc-k"]
