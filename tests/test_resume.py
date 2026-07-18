"""Crash-resume: a killed run resumes from its SQLite checkpoint without
re-running earlier nodes (decision 3.4: SqliteSaver, thread_id = run id)."""

from pathlib import Path

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from fie.state import CriticOutput, DraftClaim, EngineState, SynthesisOutput
from tests.fakes import StructuredFakeModel, StubRetriever, make_chunks, make_graph

QUESTION = "What were Apple's total net sales?"
NUMERIC_SOURCE = "(In millions) Total net sales were $ 391,035 for fiscal 2024."
TRUE_NUMERIC = "Apple reported total net sales of $391.0 billion"
ANSWER = f"{TRUE_NUMERIC} [c1]."


async def test_crash_after_verify_resumes_without_rerunning_earlier_nodes(
    tmp_path: Path,
) -> None:
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE}))
    synthesis = StructuredFakeModel(
        outputs=[
            SynthesisOutput(
                answer=ANSWER,
                claims=[
                    DraftClaim(text=TRUE_NUMERIC, claim_type="numeric", source_chunk_ids=["c1"])
                ],
            )
        ]
    )
    # The critic crashes on its first call (after the verify checkpoint), then
    # works on resume.
    critic = StructuredFakeModel(fail_times=1, outputs=[CriticOutput(passed=True)])

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
        graph = make_graph(
            retriever=retriever, synthesis=synthesis, critic=critic, checkpointer=saver
        )
        config: RunnableConfig = {"configurable": {"thread_id": "run-crash-test"}}

        with pytest.raises(RuntimeError, match="transient fake failure"):
            await graph.ainvoke(EngineState(question=QUESTION), config)

        queries_before = list(retriever.queries)
        synthesis_calls_before = len(synthesis.calls)

        # Resume: invoke with None under the same thread_id.
        result = await graph.ainvoke(None, config)

    state = EngineState.model_validate(result)
    assert state.critic_passed is True
    assert state.draft_answer == ANSWER
    assert state.claims[0].status == "grounded"
    assert state.error is None
    # Earlier nodes were not re-run on resume.
    assert retriever.queries == queries_before == [QUESTION]
    assert len(synthesis.calls) == synthesis_calls_before == 1
    # The critic was called twice in total: the crash and the resume.
    assert len(critic.calls) == 2
