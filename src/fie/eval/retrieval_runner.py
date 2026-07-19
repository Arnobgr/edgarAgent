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
from fie.eval.datasets import EVAL_DIR, RetrievalQuery, load_jsonl
from fie.eval.metrics import mrr, ndcg_at_10, recall_at_k
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Evaluate retriever variant(s) on the labeled query set."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    settings = get_settings()
    queries_path = Path(queries) if queries else EVAL_DIR / "retrieval_queries.jsonl"
    query_set = load_jsonl(queries_path, RetrievalQuery)
    if embedding == "both":
        embeddings = list(settings.embedding_models)
    else:
        embeddings = [embedding or settings.embedding_provider]
    reranks = [True, False] if rerank == "both" else [rerank == "on"]

    results = []
    for provider in embeddings:
        for use_rerank in reranks:
            name = f"{provider}+rerank" if use_rerank else f"{provider}+rrf-only"
            typer.echo(f"Evaluating {name} over {len(query_set)} queries…")
            retriever = _build_retriever(settings, provider, use_rerank)
            results.append(evaluate_variant(retriever, query_set, name))
    report = write_report(results, query_set, EVAL_DIR / "reports")
    typer.echo(report.read_text())
    typer.echo(f"Report: {report}")


def main() -> None:
    app()
