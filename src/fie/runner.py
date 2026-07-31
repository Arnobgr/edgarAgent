"""Production wiring: build the graph over the real corpus and run it.

Shared by the CLI (`fie ask` / `fie resume`) and the end-to-end eval runner
(`uv run eval-agent`) so both execute the identical system under test.
"""

import logging

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from fie.config import Settings
from fie.graph import GraphLimits, build_graph
from fie.guardrails import BudgetTracker
from fie.llm import bind_structured, get_model
from fie.observability import get_langfuse_handler, trace_metadata
from fie.state import EngineState, RunBudget, RunResult, build_run_result
from fie.tools.fact_lookup import FactLookupOutput, XbrlFactLookup
from fie.tools.reconcile import FactMappingOutput, XbrlNumericReconciler
from fie.tools.retrieval import (
    Bm25SparseEncoder,
    NoopReranker,
    QdrantHybridRetriever,
    Reranker,
    Retriever,
    get_reranker,
)

logger = logging.getLogger(__name__)


def build_production_retriever(settings: Settings) -> Retriever:
    """Qdrant hybrid retriever over the ingested corpus. Fails with a
    pointed message when the corpus hasn't been ingested yet."""
    from qdrant_client import QdrantClient

    from fie.ingest.embed import get_embedder

    collection = settings.qdrant_collection()
    client = QdrantClient(url=settings.qdrant_url)
    try:
        exists = client.collection_exists(collection)
    except Exception as exc:
        raise RuntimeError(
            f"Qdrant is unreachable at {settings.qdrant_url} — start it with "
            "`docker compose -f docker-compose.dev.yml up -d qdrant`"
        ) from exc
    if not exists:
        raise RuntimeError(
            f"Qdrant collection {collection!r} does not exist — run "
            "`fie ingest` first (it fetches, parses, embeds and upserts the corpus)"
        )
    reranker: Reranker
    if settings.reranker_provider == "voyage" and not settings.voyage_api_key.get_secret_value():
        logger.warning("no VOYAGE_API_KEY: reranker disabled, keeping RRF order")
        reranker = NoopReranker()
    else:
        reranker = get_reranker(settings)
    return QdrantHybridRetriever(
        client,
        collection,
        embedder=get_embedder(settings),
        sparse_encoder=Bm25SparseEncoder(),
        reranker=reranker,
        corpus_tickers=settings.corpus_tickers,
        prefetch_limit=settings.retrieval_prefetch,
        candidates=settings.retrieval_candidates,
    )


def build_production_reconciler(settings: Settings) -> XbrlNumericReconciler:
    """XBRL-exact reconciliation over the local facts store (ADR 0012)."""
    from fie.ingest.store import IngestStore

    if not settings.ingest_db_path.exists():
        raise RuntimeError(
            f"facts store {settings.ingest_db_path} does not exist — run `fie ingest` first"
        )
    mapper = bind_structured(get_model("fact_mapping", settings), FactMappingOutput)
    return XbrlNumericReconciler(IngestStore(settings.ingest_db_path), mapper)


def build_production_fact_lookup(settings: Settings) -> XbrlFactLookup:
    """The facts store on the *research* path — the half of decision §3.3
    that was never built (ADR 0022). Its own store connection: the graph
    reads it from research branches while `verify` reads the reconciler's,
    and one connection shared across both would widen the close/query race
    ADR 0019 closed."""
    from fie.ingest.store import IngestStore

    if not settings.ingest_db_path.exists():
        raise RuntimeError(
            f"facts store {settings.ingest_db_path} does not exist — run `fie ingest` first"
        )
    mapper = bind_structured(get_model("fact_lookup", settings), FactLookupOutput)
    return XbrlFactLookup(IngestStore(settings.ingest_db_path), mapper)


async def answer_question(
    *,
    question: str | None,
    run_id: str,
    settings: Settings,
    run_name: str = "fie-ask",
    extra_callbacks: list[BaseCallbackHandler] | None = None,
) -> RunResult:
    """Run (question given) or resume (question None) one engine run over
    the production corpus, checkpointed and traced."""
    retriever = build_production_retriever(settings)
    reconciler = build_production_reconciler(settings)
    fact_lookup = build_production_fact_lookup(settings)
    tracker = BudgetTracker(
        settings.budget_max_tokens, settings.budget_max_cost_usd, settings.model_prices
    )
    trace = get_langfuse_handler(run_id, settings)
    callbacks: list[BaseCallbackHandler] = [tracker, *(extra_callbacks or [])]
    if trace is not None:
        callbacks.append(trace.handler)

    checkpoint_path = settings.data_dir / "checkpoints.db"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            graph = build_graph(
                retriever=retriever,
                decompose_model=get_model("decompose", settings),
                synthesis_model=get_model("synthesis", settings),
                qualitative_model=get_model("verify_qualitative", settings),
                forward_model=get_model("verify_forward", settings),
                critic_model=get_model("critic", settings),
                rewrite_model=get_model("query_rewrite", settings),
                reconciler=reconciler,
                tracker=tracker,
                fact_lookup=fact_lookup,
                limits=GraphLimits(
                    max_verify_iterations=settings.max_verify_iterations,
                    max_critic_iterations=settings.max_critic_iterations,
                    max_sub_questions=settings.max_sub_questions,
                    max_concurrent_research=settings.max_concurrent_research,
                    node_timeout_seconds=settings.node_timeout_seconds,
                    retry_attempts=settings.llm_retry_attempts,
                    retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
                ),
                k=settings.retrieval_k,
                chunks_per_sub_question=settings.chunks_per_sub_question,
                checkpointer=saver,
            )
            config: RunnableConfig = {
                "run_name": run_name,
                "configurable": {"thread_id": run_id},
                "metadata": trace_metadata(run_id),
                "callbacks": callbacks,
            }
            initial = (
                None
                if question is None
                else EngineState(
                    question=question,
                    run_budget=RunBudget(
                        max_tokens=settings.budget_max_tokens,
                        max_cost_usd=settings.budget_max_cost_usd,
                    ),
                )
            )
            raw = await graph.ainvoke(initial, config)
    finally:
        reconciler.close()
        fact_lookup.close()

    if trace is not None:
        trace.flush()
        logger.info("Langfuse trace: %s", trace.trace_url)
    return build_run_result(run_id, EngineState.model_validate(raw))
