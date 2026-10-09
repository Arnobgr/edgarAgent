"""`uv run eval-seed` — generate eval-set candidates for hand-verification.

Three artifacts, all derived from the ingested corpus so they can't drift
from it:

- **Retrieval query candidates** (`eval/retrieval_queries.candidates.jsonl`):
  a cheap model proposes analyst queries per section; the operator hand-verifies
  and promotes them into `eval/retrieval_queries.jsonl` (label_status
  "verified"). The committed authored set stays the base; this adds volume.
- **Verifier labeled claims** (`eval/verifier_claims.jsonl`): real claims
  rendered from XBRL facts + systematic perturbations, true verdicts known
  by construction (no hand-labeling needed — the generator is the label).
- **Figure retrieval labels** (`eval/figure_queries.jsonl`): "query → the
  chunk carrying this figure", built by finding each fact's filing-scale
  mantissa in the chunk text. Also self-labeling, and the only measurement
  of figure *delivery* — ADR 0011's section labels cannot see it (ADR 0020).

The first needs a model; the other two are free, so `--skip-queries`
regenerates them with no API keys.
"""

import logging
import random
from pathlib import Path

import typer
from pydantic import BaseModel, Field

from fie.config import get_settings
from fie.eval.datasets import (
    EVAL_DIR,
    GoldQuestion,
    RetrievalQuery,
    SectionRef,
    load_jsonl,
    save_jsonl,
)
from fie.eval.figures import build_figure_queries
from fie.eval.perturb import build_labeled_claims
from fie.ingest.parse import ParsedFiling
from fie.ingest.store import IngestStore
from fie.llm import bind_structured, get_model

logger = logging.getLogger(__name__)

app = typer.Typer()


class _QueryIdeas(BaseModel):
    queries: list[str] = Field(default_factory=list)


_SEED_SYSTEM = (
    "You write realistic equity-analyst search queries that this filing "
    "section (and only plausibly this section) would answer. Return 2 short "
    "queries. Mention the company naturally. No question marks needed."
)


@app.command()
def run(
    max_sections: int = typer.Option(30, help="Sections to sample for query seeding."),
    skip_queries: bool = typer.Option(False, help="Only regenerate verifier claims."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Generate retrieval-query candidates + the verifier labeled-claim set."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    settings = get_settings()
    store = IngestStore(settings.ingest_db_path)
    try:
        claims = build_labeled_claims(store)
        if not claims:
            typer.secho("facts store is empty — run `fie ingest` first", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        save_jsonl(EVAL_DIR / "verifier_claims.jsonl", claims)
        typer.echo(f"verifier_claims.jsonl: {len(claims)} labeled claims")

        gold = load_jsonl(EVAL_DIR / "gold_set.jsonl", GoldQuestion)
        figures = build_figure_queries(store, settings.data_dir / "chunks", gold)
        save_jsonl(EVAL_DIR / "figure_queries.jsonl", figures)
        n_gold = sum(1 for f in figures if f.source == "gold")
        typer.echo(f"figure_queries.jsonl: {len(figures)} figure labels ({n_gold} on gold wording)")

        if skip_queries:
            return
        candidates = _seed_queries(settings.data_dir / "parsed", max_sections)
        save_jsonl(EVAL_DIR / "retrieval_queries.candidates.jsonl", candidates)
        typer.echo(
            f"retrieval_queries.candidates.jsonl: {len(candidates)} candidates "
            "(hand-verify, then merge into retrieval_queries.jsonl)"
        )
    finally:
        store.close()


def _seed_queries(parsed_dir: Path, max_sections: int) -> list[RetrievalQuery]:
    model = bind_structured(get_model("eval_seed"), _QueryIdeas)
    rng = random.Random(20260719)
    sections: list[tuple[ParsedFiling, str, str]] = []
    for path in sorted(parsed_dir.glob("*.json")):
        filing = ParsedFiling.model_validate_json(path.read_text())
        sections.extend((filing, name, text) for name, text in filing.sections.items())
    rng.shuffle(sections)

    candidates: list[RetrievalQuery] = []
    for i, (filing, name, text) in enumerate(sections[:max_sections]):
        prompt = (
            f"Company: {filing.ticker} · Form: {filing.form_type} · "
            f"Period: {filing.fiscal_period} · Section: {name}\n\n{text[:4000]}"
        )
        try:
            ideas = model.invoke([("system", _SEED_SYSTEM), ("human", prompt)])
            queries = ideas.queries if isinstance(ideas, _QueryIdeas) else []
        except Exception as exc:
            logger.warning("seeding failed for %s %s: %s", filing.accession_no, name, exc)
            continue
        candidates.extend(
            RetrievalQuery(
                id=f"rq-cand-{i:03d}-{j}",
                query=query,
                relevant=[
                    SectionRef(
                        ticker=filing.ticker,
                        form_type=filing.form_type,
                        section=name,
                        fiscal_period=filing.fiscal_period,
                    )
                ],
                label_status="seed",
            )
            for j, query in enumerate(queries[:2])
        )
    return candidates


def main() -> None:
    """Console entry point for `eval-seed`."""
    app()
