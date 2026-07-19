"""CLI entrypoints: `fie ask "..."`, `fie resume <run-id>`, `fie ingest`."""

import asyncio
import logging

import typer

from fie.config import get_settings
from fie.observability import new_run_id
from fie.runner import answer_question
from fie.state import RunResult

app = typer.Typer(no_args_is_help=True, help="Financial Intelligence Engine")

MAX_SPAN_CHARS = 100


@app.callback()
def main() -> None:
    """Financial Intelligence Engine — analyst questions over SEC filings."""


@app.command()
def ask(
    question: str = typer.Argument(..., help="The analyst question to answer."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show info-level logs."),
    json_output: bool = typer.Option(False, "--json", help="Print the RunResult JSON contract."),
) -> None:
    """Answer a question over the ingested corpus (TSLA/AAPL/NVDA filings),
    with a grounding table. Requires `fie ingest` to have run."""
    result = asyncio.run(_run(question=question, run_id=new_run_id(), verbose=verbose))
    _print_result(result, json_output)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Run id of the interrupted run."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show info-level logs."),
    json_output: bool = typer.Option(False, "--json", help="Print the RunResult JSON contract."),
) -> None:
    """Resume a killed run from its checkpoint (completed nodes are not re-run)."""
    result = asyncio.run(_run(question=None, run_id=run_id, verbose=verbose))
    _print_result(result, json_output)


@app.command()
def ingest(
    tickers: str = typer.Option(
        "", "--tickers", help="Comma-separated tickers (default: configured corpus)."
    ),
    years: int = typer.Option(0, "--years", help="Fiscal years of filings (default: config)."),
    embedding: str = typer.Option(
        "", "--embedding", help="Embedding provider for this run: voyage | openai."
    ),
    skip_embed: bool = typer.Option(
        False, "--skip-embed", help="Stop after parse/chunk/facts (no API keys needed)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show info-level logs."),
) -> None:
    """Fetch → parse → chunk → embed → upsert the corpus (resumable).

    Populates Qdrant (chunk vectors) and SQLite (manifest + XBRL facts).
    Re-running skips everything already ingested.
    """
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    from fie.ingest import run_ingest

    settings = get_settings()
    report = run_ingest(
        settings,
        tickers=[t.strip().upper() for t in tickers.split(",") if t.strip()] or None,
        years=years or None,
        embedding_provider=embedding or None,
        skip_embed=skip_embed,
    )
    typer.echo("\n=== Ingest report ===")
    for line in report.lines():
        typer.echo(line)
    if report.failures:
        raise typer.Exit(code=1)


async def _run(*, question: str | None, run_id: str, verbose: bool) -> RunResult:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    settings = get_settings()
    typer.echo(f"Run: {run_id} (resume an interrupted run with `fie resume {run_id}`)")
    try:
        return await answer_question(question=question, run_id=run_id, settings=settings)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


def _print_result(result: RunResult, json_output: bool) -> None:
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
        return

    if result.sub_questions:
        typer.echo("\n=== Decomposition ===")
        for sq in result.sub_questions:
            typer.echo(f"[{sq.status:>10}] {sq.id}: {sq.text}")

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
