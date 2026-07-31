"""`uv run eval-retrieval` — the retrieval eval + benchmark harness.

Runs the labeled query set against one or more retriever variants
(embedding provider x reranker on/off), computes recall@{5,10,20}, MRR and
nDCG@10 per variant, and writes a JSON + markdown report under
`eval/reports/`. This is the harness that decides the embedding and
reranker ADRs (0011): run `--embedding both --rerank both` once both
collections are ingested, and record the measured numbers.
"""

import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import typer

from fie.config import Settings, get_settings
from fie.eval.datasets import EVAL_DIR, FigureQuery, RetrievalQuery, load_jsonl
from fie.eval.metrics import figure_rank, figure_recall_at_k, mrr, ndcg_at_10, recall_at_k
from fie.tools.retrieval import Retriever

logger = logging.getLogger(__name__)

app = typer.Typer()

RECALL_KS = (5, 10, 20)


@dataclass
class VariantResult:
    """Aggregated metrics for one retriever variant."""

    variant: str
    n_queries: int
    recall_at_5: float
    recall_at_10: float
    recall_at_20: float
    mrr: float
    ndcg_at_10: float


def evaluate_variant(
    retriever: Retriever, queries: list[RetrievalQuery], name: str
) -> VariantResult:
    """Run every query through one retriever and aggregate the metrics."""
    recalls: dict[int, list[float]] = {k: [] for k in RECALL_KS}
    mrrs: list[float] = []
    ndcgs: list[float] = []
    for query in queries:
        retrieved = retriever.retrieve(query.query, k=max(RECALL_KS))
        for k in RECALL_KS:
            recalls[k].append(recall_at_k(retrieved, query.relevant, k))
        mrrs.append(mrr(retrieved, query.relevant))
        ndcgs.append(ndcg_at_10(retrieved, query.relevant))
    return VariantResult(
        variant=name,
        n_queries=len(queries),
        recall_at_5=_mean(recalls[5]),
        recall_at_10=_mean(recalls[10]),
        recall_at_20=_mean(recalls[20]),
        mrr=_mean(mrrs),
        ndcg_at_10=_mean(ndcgs),
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# Production consumes `retrieval_k` (8); the deeper points say how much a
# bigger k would buy, which is the only honest basis for changing it.
FIGURE_KS = (5, 8, 10, 20, 50)


@dataclass
class FigureVariantResult:
    """Figure-delivery metrics for one retriever variant."""

    variant: str
    n_queries: int
    recall: dict[int, float]
    ranks: dict[str, int | None]


def evaluate_figures(
    retriever: Retriever, queries: list[FigureQuery], name: str
) -> FigureVariantResult:
    """Rank of the chunk carrying each figure, per query."""
    ranks = {
        query.id: figure_rank(
            retriever.retrieve(query.query, k=max(FIGURE_KS)), query.relevant_chunk_ids
        )
        for query in queries
    }
    ordered = list(ranks.values())
    return FigureVariantResult(
        variant=name,
        n_queries=len(queries),
        recall={k: figure_recall_at_k(ordered, k) for k in FIGURE_KS},
        ranks=ranks,
    )


def write_figure_report(
    results: list[FigureVariantResult], queries: list[FigureQuery], out_dir: Path
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = {
        "generated": stamp,
        "n_queries": len(queries),
        "n_gold_wording": sum(1 for q in queries if q.source == "gold"),
        "variants": [asdict(r) for r in results],
    }
    (out_dir / f"figures-{stamp}.json").write_text(json.dumps(payload, indent=2))

    header = " | ".join(f"recall@{k}" for k in FIGURE_KS)
    lines = [
        "# Figure-level retrieval report",
        "",
        f"Generated: {stamp} · {len(queries)} figures · "
        f"{payload['n_gold_wording']} on gold question wording",
        "",
        f"| variant | {header} |",
        "|---" * (len(FIGURE_KS) + 1) + "|",
    ]
    lines.extend(
        f"| {r.variant} | " + " | ".join(f"{r.recall[k]:.3f}" for k in FIGURE_KS) + " |"
        for r in results
    )
    by_id = {q.id: q for q in queries}
    lines += [
        "",
        "## Rank of the chunk carrying each figure (— = outside the top 50)",
        "",
        "| figure | source | " + " | ".join(r.variant for r in results) + " |",
        "|---|---|" + "---|" * len(results),
    ]
    for query_id in sorted(by_id):
        query = by_id[query_id]
        cells = " | ".join(_rank_cell(r.ranks.get(query_id)) for r in results)
        lines.append(f"| {query.description} | {query.source} | {cells} |")
    md_path = out_dir / f"figures-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n")
    return md_path


def _rank_cell(rank: int | None) -> str:
    return "—" if rank is None else str(rank)


def _build_retriever(settings: Settings, embedding: str, rerank: bool) -> Retriever:
    from qdrant_client import QdrantClient

    from fie.ingest.embed import get_embedder
    from fie.tools.retrieval import (
        Bm25SparseEncoder,
        NoopReranker,
        QdrantHybridRetriever,
        get_reranker,
    )

    collection = settings.qdrant_collection(embedding)
    client = QdrantClient(url=settings.qdrant_url)
    if not client.collection_exists(collection):
        raise RuntimeError(
            f"collection {collection!r} missing — run `fie ingest --embedding {embedding}` first"
        )
    return QdrantHybridRetriever(
        client,
        collection,
        embedder=get_embedder(settings, embedding),
        sparse_encoder=Bm25SparseEncoder(),
        reranker=get_reranker(settings) if rerank else NoopReranker(),
        corpus_tickers=settings.corpus_tickers,
        prefetch_limit=settings.retrieval_prefetch,
        candidates=settings.retrieval_candidates,
    )


def write_report(
    results: list[VariantResult], queries: list[RetrievalQuery], out_dir: Path
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    seed_count = sum(1 for q in queries if q.label_status == "seed")
    payload = {
        "generated": stamp,
        "n_queries": len(queries),
        "unverified_seed_labels": seed_count,
        "variants": [asdict(r) for r in results],
    }
    (out_dir / f"retrieval-{stamp}.json").write_text(json.dumps(payload, indent=2))

    lines = [
        "# Retrieval eval report",
        "",
        f"Generated: {stamp} · {len(queries)} queries"
        + (f" · ⚠ {seed_count} labels still seed (unverified)" if seed_count else ""),
        "",
        "| variant | recall@5 | recall@10 | recall@20 | MRR | nDCG@10 |",
        "|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {r.variant} | {r.recall_at_5:.3f} | {r.recall_at_10:.3f} | "
        f"{r.recall_at_20:.3f} | {r.mrr:.3f} | {r.ndcg_at_10:.3f} |"
        for r in results
    )
    md_path = out_dir / f"retrieval-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n")
    return md_path


@app.command()
def run(
    embedding: str = typer.Option(
        "", "--embedding", help="voyage | openai | both (default: configured provider)."
    ),
    rerank: str = typer.Option("on", "--rerank", help="on | off | both."),
    queries: str = typer.Option("", "--queries", help="Query-set JSONL path override."),
    figures: bool = typer.Option(
        False,
        "--figures",
        help="Score figure delivery against eval/figure_queries.jsonl instead of section labels.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Evaluate retriever variant(s) on the labeled query set."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    settings = get_settings()
    default_set = "figure_queries.jsonl" if figures else "retrieval_queries.jsonl"
    queries_path = Path(queries) if queries else EVAL_DIR / default_set
    if embedding == "both":
        embeddings = list(settings.embedding_models)
    else:
        embeddings = [embedding or settings.embedding_provider]
    reranks = [True, False] if rerank == "both" else [rerank == "on"]

    variants = [
        (f"{provider}+rerank" if use_rerank else f"{provider}+rrf-only", provider, use_rerank)
        for provider in embeddings
        for use_rerank in reranks
    ]
    if figures:
        figure_set = load_jsonl(queries_path, FigureQuery)
        figure_results = []
        for name, provider, use_rerank in variants:
            typer.echo(f"Evaluating {name} over {len(figure_set)} figures…")
            retriever = _build_retriever(settings, provider, use_rerank)
            figure_results.append(evaluate_figures(retriever, figure_set, name))
        report = write_figure_report(figure_results, figure_set, EVAL_DIR / "reports")
    else:
        query_set = load_jsonl(queries_path, RetrievalQuery)
        results = []
        for name, provider, use_rerank in variants:
            typer.echo(f"Evaluating {name} over {len(query_set)} queries…")
            retriever = _build_retriever(settings, provider, use_rerank)
            results.append(evaluate_variant(retriever, query_set, name))
        report = write_report(results, query_set, EVAL_DIR / "reports")
    typer.echo(report.read_text())
    typer.echo(f"Report: {report}")


def main() -> None:
    app()
