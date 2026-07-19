"""Typed eval datasets (versioned JSONL under `eval/`)."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from fie.state import ClaimType, VerdictLabel

EVAL_DIR = Path("eval")


class SectionRef(BaseModel):
    """A relevance label at (ticker, form, section) granularity — stable
    across re-chunking, unlike chunk ids. `section` matches as a
    case-insensitive substring of the retrieved chunk's section; an empty
    section matches any section of that filing kind."""

    ticker: str
    form_type: str
    section: str = ""
    fiscal_period: str = ""  # e.g. "FY2025", "Q2 2026"; empty = any period

    def matches(self, *, ticker: str, form_type: str, section: str, fiscal_period: str) -> bool:
        if self.ticker.upper() != ticker.upper():
            return False
        if not form_type.upper().startswith(self.form_type.upper()):
            return False
        if self.section and self.section.lower() not in section.lower():
            return False
        return not (self.fiscal_period and self.fiscal_period.lower() != fiscal_period.lower())


class RetrievalQuery(BaseModel):
    """One labeled retrieval-eval query (workstream C.1)."""

    id: str
    query: str
    relevant: list[SectionRef]
    # "seed" until hand-verified (the operator reviews), then "verified".
    label_status: Literal["seed", "verified"] = "seed"


class ExpectedFact(BaseModel):
    """One expected key fact of a gold answer. Numeric facts reference the
    XBRL store (concept terms + period) so the expected value is *pulled
    from the facts table* at eval time, never hand-copied."""

    kind: Literal["xbrl", "text"] = "xbrl"
    ticker: str = ""
    concept_terms: list[str] = Field(default_factory=list)
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    unit: str | None = None
    description: str = ""


class GoldQuestion(BaseModel):
    """One end-to-end gold question (workstream C.2)."""

    id: str
    question: str
    category: Literal[
        "single_numeric", "multi_period_trend", "cross_company", "qualitative", "unanswerable"
    ]
    answerable: bool = True
    expected_facts: list[ExpectedFact] = Field(default_factory=list)
    expected_sources: list[SectionRef] = Field(default_factory=list)
    label_status: Literal["seed", "verified"] = "seed"


class LabeledClaim(BaseModel):
    """One claim with a known true verdict, for verifier P/R (C.3). Real
    claims carry `grounded`; systematically perturbed ones (digits changed,
    periods shifted, sentiment inverted) carry `unsupported` by
    construction."""

    id: str
    text: str
    claim_type: ClaimType
    true_verdict: VerdictLabel
    perturbation: str = ""  # "", "digit", "period", "scale", "sentiment"
    ticker: str = ""
    evidence_text: str = ""  # cited chunk text for LLM-judged claim types


def load_jsonl[ModelT: BaseModel](path: Path, model: type[ModelT]) -> list[ModelT]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(model.model_validate_json(line))
    return rows


def save_jsonl(path: Path, rows: Sequence[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows))


def load_baselines(path: Path) -> dict[str, float | None]:
    data = json.loads(path.read_text())
    metrics: dict[str, float | None] = data.get("metrics", {})
    return metrics
