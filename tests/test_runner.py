"""Production wiring guards that fail fast without the corpus — no network.

`build_production_*` refuse to run before the corpus is ingested, with a
pointed message. The facts-store guards check the SQLite path and raise before
any model or client is constructed, so they are exercisable offline.
"""

from pathlib import Path

import pytest

from fie.config import Settings
from fie.runner import build_production_fact_lookup, build_production_reconciler


def test_reconciler_without_the_facts_store_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="facts store"):
        build_production_reconciler(Settings(data_dir=tmp_path))


def test_fact_lookup_without_the_facts_store_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="facts store"):
        build_production_fact_lookup(Settings(data_dir=tmp_path))
