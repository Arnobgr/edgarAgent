"""Phase 2 fan-out behavior with fakes — no network.

Covers the decomposer (multi-part split and trivial passthrough), the
Send-based parallel research fan-out, the accumulating evidence channel,
branch-failure isolation, budget aggregation under concurrency, the
targeted loop-back, and the bounded critic→decompose edge.
"""

import asyncio
from typing import Any

from fie.guardrails import BudgetTracker
from fie.state import (
    CriticOutput,
    DecomposedSubQuestion,
    DecomposeOutput,
    DraftClaim,
    EngineState,
    EvidenceChunk,
    JudgeOutput,
    SynthesisOutput,
    merge_evidence,
)
from tests.fakes import StructuredFakeModel, StubRetriever, fast_limits, make_chunks, make_graph

QUESTION = "How has the margin profile and capital allocation shifted, and what are the risks?"

SUB_A = "How has the margin profile shifted?"
SUB_B = "How has capital allocation shifted?"
SUB_C = "What are the key risks?"


def _three_part_decompose() -> DecomposeOutput:
    return DecomposeOutput(
        sub_questions=[
            DecomposedSubQuestion(text=SUB_A, rationale="margins are one line of research"),
            DecomposedSubQuestion(text=SUB_B, rationale="capital allocation is separate"),
            DecomposedSubQuestion(text=SUB_C, rationale="risks are separate"),
        ]
    )


def _chunk(chunk_id: str, text: str) -> EvidenceChunk:
    return make_chunks({chunk_id: text})[0]


class RoutingRetriever:
    """Returns per-query chunks; records every query (thread-safe enough
    under the GIL for list appends from `asyncio.to_thread` branches)."""

    def __init__(self, by_query: dict[str, list[EvidenceChunk]]) -> None:
        self.by_query = by_query
        self.queries: list[str] = []

    def retrieve(self, query: str, k: int = 8) -> list[EvidenceChunk]:
        self.queries.append(query)
        return self.by_query.get(query, [])[:k]


class ExplodingRetriever(RoutingRetriever):
    """Raises for designated queries — simulates one branch failing."""

    def __init__(self, by_query: dict[str, list[EvidenceChunk]], failing_queries: set[str]) -> None:
        super().__init__(by_query)
        self.failing_queries = failing_queries

    def retrieve(self, query: str, k: int = 8) -> list[EvidenceChunk]:
        if query in self.failing_queries:
            self.queries.append(query)
            raise ValueError(f"retrieval backend down for {query!r}")
        return super().retrieve(query, k)


def _human_text(messages: list[Any]) -> str:
    return str(messages[-1].content)


async def test_trivial_question_passes_through_as_single_subquestion() -> None:
    """A single-part split (even a reworded one) becomes exactly one
    sub-question carrying the original question verbatim — no ceremony."""
    retriever = StubRetriever(make_chunks())
    decompose = StructuredFakeModel(
        outputs=[
            DecomposeOutput(
                sub_questions=[DecomposedSubQuestion(text="a reworded version", rationale="")]
            )
        ]
    )
    synthesis = StructuredFakeModel(outputs=[SynthesisOutput(answer="Answer.", claims=[])])
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(retriever=retriever, synthesis=synthesis, decompose=decompose, critic=critic)

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert len(decompose.calls) == 1
    assert [sq.text for sq in state.sub_questions] == [QUESTION]
    assert state.sub_questions[0].status == "researched"
    assert retriever.queries == [QUESTION]
    # A single sub-question keeps the flat evidence packing: no per-sub-
    # question sectioning ceremony in the synthesis prompt.
    assert "## Sub-question" not in _human_text(synthesis.calls[0])
    assert state.error is None


async def test_multipart_question_fans_out_one_parallel_branch_per_subquestion() -> None:
    retriever = RoutingRetriever(
        {
            SUB_A: [_chunk("m1", "Gross margin expanded to 46.2% on services mix.")],
            SUB_B: [_chunk("m2", "The buyback authorization increased by $110 billion.")],
            SUB_C: [_chunk("m3", "Supply concentration remains a principal risk.")],
        }
    )
    decompose = StructuredFakeModel(outputs=[_three_part_decompose()])
    synthesis = StructuredFakeModel(
        outputs=[
            SynthesisOutput(
                answer="Margins up [m1]. Buybacks up [m2]. Risks remain [m3].",
                claims=[
                    DraftClaim(
                        text="margin claim", claim_type="qualitative", source_chunk_ids=["m1"]
                    ),
                    DraftClaim(
                        text="buyback claim", claim_type="qualitative", source_chunk_ids=["m2"]
                    ),
                    DraftClaim(
                        text="risk claim", claim_type="qualitative", source_chunk_ids=["m3"]
                    ),
                ],
            )
        ]
    )
    qualitative = StructuredFakeModel(default_output=JudgeOutput(verdict="grounded"))
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    tracker = BudgetTracker(max_tokens=10**9, max_cost_usd=10**6)
    graph = make_graph(
        retriever=retriever,
        synthesis=synthesis,
        decompose=decompose,
        qualitative=qualitative,
        critic=critic,
        tracker=tracker,
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    # One branch per sub-question (parallel, so order is not fixed).
    assert set(retriever.queries) == {SUB_A, SUB_B, SUB_C}
    assert len(retriever.queries) == 3
    assert [sq.status for sq in state.sub_questions] == ["researched"] * 3

    # Every branch's evidence landed in the accumulating channel, tagged
    # with its branch's sub-question.
    by_chunk = {c.chunk_id: c.sub_question_id for c in state.evidence}
    assert by_chunk == {"m1": "sq1-1", "m2": "sq1-2", "m3": "sq1-3"}

    # Fan-in synthesis saw all three sub-questions' evidence, sectioned.
    prompt = _human_text(synthesis.calls[0])
    for sub_text, chunk_id in ((SUB_A, "m1"), (SUB_B, "m2"), (SUB_C, "m3")):
        assert sub_text in prompt
        assert f"[{chunk_id}]" in prompt

    # Claims trace back to the branch that retrieved their evidence.
    assert [c.sub_question_id for c in state.claims] == ["sq1-1", "sq1-2", "sq1-3"]

    # Budget stamps from parallel branches merged without conflict.
    assert state.run_budget.tokens_used == tracker.snapshot().tokens_used
    assert state.error is None


def test_evidence_reducer_merges_without_loss_or_duplication() -> None:
    a1 = _chunk("c1", "text one").model_copy(update={"sub_question_id": "sq1-1"})
    b1 = _chunk("c2", "text two").model_copy(update={"sub_question_id": "sq1-2"})
    # The same chunk retrieved by two branches: both entries kept (prompt-
    # level dedupe is context packing's job, not the reducer's).
    a1_other_branch = a1.model_copy(update={"sub_question_id": "sq1-2"})
    # A loop-back re-run of the same branch retrieving the same chunk: deduped.
    a1_again = a1.model_copy(update={"score": 0.5})

    merged = merge_evidence([a1, b1], [a1_other_branch, a1_again])

    assert [(c.chunk_id, c.sub_question_id) for c in merged] == [
        ("c1", "sq1-1"),
        ("c2", "sq1-2"),
        ("c1", "sq1-2"),
    ]
    # The first-seen chunk wins on a duplicate key.
    assert merged[0].score == a1.score


async def test_branch_failure_marks_subquestion_exhausted_and_run_survives() -> None:
    retriever = ExplodingRetriever(
        {SUB_A: [_chunk("m1", "Gross margin expanded.")]},
        failing_queries={SUB_B},
    )
    decompose = StructuredFakeModel(
        outputs=[
            DecomposeOutput(
                sub_questions=[
                    DecomposedSubQuestion(text=SUB_A, rationale=""),
                    DecomposedSubQuestion(text=SUB_B, rationale=""),
                ]
            )
        ]
    )
    synthesis = StructuredFakeModel(
        outputs=[SynthesisOutput(answer="Margins expanded [m1].", claims=[])]
    )
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(retriever=retriever, synthesis=synthesis, decompose=decompose, critic=critic)

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    # The failed branch did not kill the run.
    assert state.error is None
    assert state.draft_answer == "Margins expanded [m1]."
    statuses = {sq.text: sq.status for sq in state.sub_questions}
    assert statuses == {SUB_A: "researched", SUB_B: "exhausted"}
    failures = [r for r in state.branch_results if not r.ok]
    assert len(failures) == 1
    assert "retrieval backend down" in (failures[0].detail or "")
    # The synthesizer was told about the gap.
    prompt = _human_text(synthesis.calls[0])
    assert SUB_B in prompt
    assert "no evidence is available" in prompt
    # Only the healthy branch contributed evidence.
    assert [c.chunk_id for c in state.evidence] == ["m1"]


async def test_targeted_loop_back_reruns_exactly_one_branch() -> None:
    lie = "Capital returns were driven by Vision Pro"
    retriever = RoutingRetriever(
        {
            SUB_A: [_chunk("m1", "(In millions) Revenue was $ 100 this year.")],
            SUB_B: [_chunk("m2", "The buyback authorization increased.")],
            SUB_C: [_chunk("m3", "Management expects continued growth.")],
            lie: [_chunk("m2b", "Dividends and buybacks drove capital returns.")],
        }
    )
    decompose = StructuredFakeModel(outputs=[_three_part_decompose()])
    claims = [
        DraftClaim(text="Revenue was $100 million", claim_type="numeric", source_chunk_ids=["m1"]),
        DraftClaim(text=lie, claim_type="qualitative", source_chunk_ids=["m2"]),
        DraftClaim(
            text="Management expects continued growth",
            claim_type="forward_looking",
            source_chunk_ids=["m3"],
        ),
    ]
    synthesis = StructuredFakeModel(
        outputs=[
            SynthesisOutput(
                answer=f"$100 million [m1]. {lie} [m2]. Growth ahead [m3].", claims=claims
            ),
            SynthesisOutput(answer="Revised [m1] [m2b] [m3].", claims=claims),
        ]
    )
    # The qualitative judge rejects the lie once, then accepts on re-verify.
    qualitative = StructuredFakeModel(
        outputs=[
            JudgeOutput(verdict="unsupported", feedback="the source never mentions Vision Pro")
        ],
        default_output=JudgeOutput(verdict="grounded"),
    )
    forward = StructuredFakeModel(
        default_output=JudgeOutput(verdict="grounded", supporting_span="expects continued growth")
    )
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(
        retriever=retriever,
        synthesis=synthesis,
        decompose=decompose,
        qualitative=qualitative,
        forward=forward,
        critic=critic,
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    # Initial fan-out: three branches. Loop-back: exactly one more retrieval,
    # for the failed claim's text only.
    assert set(retriever.queries[:3]) == {SUB_A, SUB_B, SUB_C}
    assert retriever.queries[3:] == [lie]
    assert state.verify_iteration == 2

    # The failed claim traces to its branch, and the loop-back re-researched
    # that branch: the new chunk is tagged with the same sub-question.
    failed_claim = next(c for c in state.claims if c.text == lie)
    assert failed_claim.sub_question_id == "sq1-2"
    assert failed_claim.status == "grounded"
    new_chunk = next(c for c in state.evidence if c.chunk_id == "m2b")
    assert new_chunk.sub_question_id == "sq1-2"

    # The rejection feedback reached the revision prompt.
    revision_prompt = _human_text(synthesis.calls[1])
    assert "Vision Pro" in revision_prompt
    assert state.error is None


async def test_critic_redecompose_replaces_split_with_feedback() -> None:
    retriever = StubRetriever(make_chunks())
    decompose = StructuredFakeModel(
        outputs=[
            DecomposeOutput(
                sub_questions=[
                    DecomposedSubQuestion(text=SUB_A, rationale=""),
                    DecomposedSubQuestion(text=SUB_B, rationale=""),
                ]
            ),
            DecomposeOutput(),  # re-split: single-part passthrough
        ]
    )
    synthesis = StructuredFakeModel(default_output=SynthesisOutput(answer="Draft.", claims=[]))
    critic = StructuredFakeModel(
        outputs=[
            CriticOutput(
                passed=False, feedback="the risks part was never researched", re_decompose=True
            ),
            CriticOutput(passed=True),
        ]
    )
    graph = make_graph(retriever=retriever, synthesis=synthesis, decompose=decompose, critic=critic)

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert len(decompose.calls) == 2
    # The critic's feedback reached the re-decomposition prompt.
    assert "the risks part was never researched" in _human_text(decompose.calls[1])
    # The new generation replaced the old split wholesale.
    assert [sq.id for sq in state.sub_questions] == ["sq2-1"]
    assert state.decompose_iteration == 2
    assert state.critic_passed is True
    assert state.error is None


async def test_redecomposition_is_bounded_to_one_per_run() -> None:
    retriever = StubRetriever(make_chunks())
    decompose = StructuredFakeModel(default_output=DecomposeOutput())
    synthesis = StructuredFakeModel(default_output=SynthesisOutput(answer="Draft.", claims=[]))
    # The critic always demands a re-decomposition.
    critic = StructuredFakeModel(
        default_output=CriticOutput(passed=False, feedback="still mis-split", re_decompose=True)
    )
    graph = make_graph(
        retriever=retriever,
        synthesis=synthesis,
        decompose=decompose,
        critic=critic,
        limits=fast_limits(max_critic_iterations=3),
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    # Only one re-decomposition happened; the next critic failure fell back
    # to the synthesize cycle, and the run still terminated at the caps.
    assert len(decompose.calls) == 2
    assert state.decompose_iteration == 2
    assert len(synthesis.calls) == 3
    assert state.critic_iteration == 3
    assert state.critic_passed is False


async def test_budget_aggregation_is_exact_under_concurrent_recording() -> None:
    """Parallel branches report usage through the shared tracker; concurrent
    records must sum exactly (the lock, not luck)."""
    tracker = BudgetTracker(
        max_tokens=10**9, max_cost_usd=10**6, prices={"claude-opus-4-8": (5.0, 25.0)}
    )

    async def one_call() -> None:
        await asyncio.to_thread(tracker.record, 1000, 200, "claude-opus-4-8")

    await asyncio.gather(*(one_call() for _ in range(50)))

    assert tracker.tokens_used == 50 * 1200
    expected_cost = 50 * (1000 * 5.0 / 1e6 + 200 * 25.0 / 1e6)
    assert abs(tracker.cost_usd_used - expected_cost) < 1e-9
