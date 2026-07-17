"""CLI entrypoint: `fie ask "..."`."""

import logging

import typer
from langchain_core.runnables import RunnableConfig

from fie.config import get_settings
from fie.graph import build_graph
from fie.llm import get_model
from fie.observability import get_langfuse_handler, new_run_id, trace_metadata
from fie.state import EngineState
from fie.tools import BM25Retriever, chunk_document, fetch_filing

app = typer.Typer(no_args_is_help=True, help="Financial Intelligence Engine")


@app.callback()
def main() -> None:
    """Financial Intelligence Engine — analyst questions over SEC filings."""


@app.command()
def ask(
    question: str = typer.Argument(..., help="The analyst question to answer."),
    ticker: str = typer.Option("AAPL", help="Company ticker (Phase 0 corpus: AAPL)."),
    form_type: str = typer.Option("10-K", "--form", help="Filing form type."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show info-level logs."),
) -> None:
    """Answer a question over the company's latest filing, with citations."""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    settings = get_settings()
    run_id = new_run_id()

    doc = fetch_filing(ticker, form_type, settings)
    chunks = chunk_document(doc)
    typer.echo(f"Corpus: {doc.doc_id} ({len(doc.sections)} sections, {len(chunks)} chunks)")

    graph = build_graph(
        retriever=BM25Retriever(chunks),
        synthesis_model=get_model("synthesis", settings),
        k=settings.retrieval_k,
    )

    trace = get_langfuse_handler(run_id, settings)
    config: RunnableConfig = {
        "run_name": "fie-ask",
        "metadata": trace_metadata(run_id),
    }
    if trace is not None:
        config["callbacks"] = [trace.handler]

    result = graph.invoke(EngineState(question=question), config)

    typer.echo("\n=== Answer ===\n")
    typer.echo(result["draft_answer"] or "(no answer produced)")

    typer.echo("\n=== Evidence ===")
    for chunk in result["evidence"]:
        score = f"{chunk.score:.2f}" if chunk.score is not None else "-"
        typer.echo(
            f"[{chunk.chunk_id}] {chunk.section} "
            f"({chunk.form_type} {chunk.metadata.get('filing_date', '?')}, "
            f"acc {chunk.metadata.get('accession_no', '?')}, score {score})"
        )

    if trace is not None:
        trace.flush()
        typer.echo(f"\nLangfuse trace: {trace.trace_url}")
    else:
        typer.echo("\n(no Langfuse trace: keys not configured)")


if __name__ == "__main__":
    app()
