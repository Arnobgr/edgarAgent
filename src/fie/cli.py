"""CLI entrypoints: `fie ask "..."` and `fie resume <run-id>`."""

import asyncio
import logging

import typer
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from fie.config import Settings, get_settings
from fie.graph import GraphLimits, build_graph
from fie.guardrails import BudgetTracker
from fie.llm import get_model
from fie.observability import get_langfuse_handler, new_run_id, trace_metadata
from fie.state import EngineState, EvidenceChunk, RunBudget, RunResult, build_run_result
from fie.tools import BM25Retriever, InterimNumericReconciler, chunk_document, fetch_filing

app = typer.Typer(no_args_is_help=True, help="Financial Intelligence Engine")

MAX_SPAN_CHARS = 100


@app.callback()
def main() -> None:
    """Financial Intelligence Engine — analyst questions over SEC filings."""


@app.command()
def ask(
    question: str = typer.Argument(..., help="The analyst question to answer."),
    ticker: str = typer.Option("AAPL", help="Company ticker (Phase 1 corpus: AAPL)."),
    form_type: str = typer.Option("10-K", "--form", help="Filing form type."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show info-level logs."),
    json_output: bool = typer.Option(False, "--json", help="Print the RunResult JSON contract."),
) -> None:
    """Answer a question over the company's latest filing, with a grounding table."""
    result = asyncio.run(
        _run(
            question=question,
            ticker=ticker,
            form_type=form_type,
            run_id=new_run_id(),
            verbose=verbose,
        )
    )
    _print_result(result, json_output)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Run id of the interrupted run."),
    ticker: str = typer.Option("AAPL", help="Same ticker as the original run."),
    form_type: str = typer.Option("10-K", "--form", help="Same form type as the original run."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show info-level logs."),
    json_output: bool = typer.Option(False, "--json", help="Print the RunResult JSON contract."),
) -> None:
    """Resume a killed run from its checkpoint (completed nodes are not re-run)."""
    result = asyncio.run(
        _run(question=None, ticker=ticker, form_type=form_type, run_id=run_id, verbose=verbose)
    )
    _print_result(result, json_output)


async def _run(
    *, question: str | None, ticker: str, form_type: str, run_id: str, verbose: bool
) -> RunResult:
    """Build the corpus and graph, then run (question given) or resume (None)."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    settings = get_settings()

    doc = fetch_filing(ticker, form_type, settings)
    chunks = chunk_document(doc)
    typer.echo(f"Corpus: {doc.doc_id} ({len(doc.sections)} sections, {len(chunks)} chunks)")
    typer.echo(f"Run: {run_id} (resume an interrupted run with `fie resume {run_id}`)")

    tracker = BudgetTracker(
        settings.budget_max_tokens, settings.budget_max_cost_usd, settings.model_prices
    )
    trace = get_langfuse_handler(run_id, settings)
    callbacks: list[BaseCallbackHandler] = [tracker]
    if trace is not None:
        callbacks.append(trace.handler)

    checkpoint_path = settings.data_dir / "checkpoints.db"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        graph = _build_graph(settings, chunks, tracker, saver)
        config: RunnableConfig = {
            "run_name": "fie-ask",
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

    if trace is not None:
        trace.flush()
        typer.echo(f"Langfuse trace: {trace.trace_url}")
    else:
        typer.echo("(no Langfuse trace: keys not configured)")
    return build_run_result(run_id, EngineState.model_validate(raw))


def _build_graph(
    settings: Settings,
    chunks: list[EvidenceChunk],
    tracker: BudgetTracker,
    saver: AsyncSqliteSaver,
) -> CompiledStateGraph[EngineState]:
    return build_graph(
        retriever=BM25Retriever(chunks),
        synthesis_model=get_model("synthesis", settings),
        qualitative_model=get_model("verify_qualitative", settings),
        forward_model=get_model("verify_forward", settings),
        critic_model=get_model("critic", settings),
        reconciler=InterimNumericReconciler(),
        tracker=tracker,
        limits=GraphLimits(
            max_verify_iterations=settings.max_verify_iterations,
            max_critic_iterations=settings.max_critic_iterations,
            node_timeout_seconds=settings.node_timeout_seconds,
            retry_attempts=settings.llm_retry_attempts,
            retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
        ),
        k=settings.retrieval_k,
        checkpointer=saver,
    )


def _print_result(result: RunResult, json_output: bool) -> None:
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    typer.echo("\n=== Answer ===\n")
    typer.echo(result.answer or "(no answer produced)")

    typer.echo("\n=== Grounding table ===")
    for row in [*result.grounding, *result.retracted]:
        span = row.supporting_span or "-"
        if len(span) > MAX_SPAN_CHARS:
            span = span[:MAX_SPAN_CHARS] + "…"
        sources = ", ".join(
            f"{s.chunk_id} ({s.section}, acc {s.accession_no or '?'})" for s in row.sources
        )
        typer.echo(f"[{row.status.upper():>11}] ({row.claim_type}) {row.text}")
        typer.echo(f"              method: {row.method or '-'} | sources: {sources or '-'}")
        typer.echo(f"              span: {span}")
        if row.feedback:
            typer.echo(f"              feedback: {row.feedback}")

    typer.echo(
        f"\nIterations: verify={result.verify_iterations}, critic={result.critic_iterations}"
        f" (critic passed: {result.critic_passed})"
    )
    budget = result.budget
    typer.echo(
        f"Budget: {budget.tokens_used:,}/{budget.max_tokens:,} tokens, "
        f"${budget.cost_usd_used:.4f}/${budget.max_cost_usd:.2f}"
    )
    if result.error:
        typer.secho(f"\nRun ended with error: {result.error}", fg=typer.colors.RED)


if __name__ == "__main__":
    app()
