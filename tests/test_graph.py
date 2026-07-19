"""Phase 1 graph behavior with fakes — no network.

The planted-lie test is the phase's signature test: a fabricated figure and a
fabricated qualitative claim must be flagged, loop back with feedback, and be
retracted (never silently dropped) once iterations are exhausted.
"""

from typing import Any

from fie.graph import GraphLimits
from fie.graph.build import RETRACTED_HEADER
from fie.state import (
    CriticOutput,
    DraftClaim,
    EngineState,
    JudgeOutput,
    SynthesisOutput,
    build_run_result,
)
from fie.tools.reconcile import ReconcileOutcome
from tests.fakes import (
    BudgetTracker,
    SpyReconciler,
    StructuredFakeModel,
    StubRetriever,
    make_chunks,
    make_graph,
)

QUESTION = "What were Apple's total net sales?"

NUMERIC_SOURCE = "(In millions) Total net sales were $ 391,035 for fiscal 2024, up 2%."
QUAL_SOURCE = "Services revenue grew due to advertising, cloud services and the App Store."

TRUE_NUMERIC = "Apple reported total net sales of $391.0 billion"
LIE_NUMERIC = "Apple also generated $500.0 billion from Vision Pro sales"
LIE_QUAL = "Growth was driven entirely by Vision Pro demand"


def _lie_synthesis_output() -> SynthesisOutput:
    return SynthesisOutput(
        answer=f"{TRUE_NUMERIC} [c1]. {LIE_NUMERIC} [c1]. {LIE_QUAL} [c2].",
        claims=[
            DraftClaim(text=TRUE_NUMERIC, claim_type="numeric", source_chunk_ids=["c1"]),
            DraftClaim(text=LIE_NUMERIC, claim_type="numeric", source_chunk_ids=["c1"]),
            DraftClaim(text=LIE_QUAL, claim_type="qualitative", source_chunk_ids=["c2"]),
        ],
    )


def _human_text(messages: list[Any]) -> str:
    return str(messages[-1].content)


async def test_planted_lie_is_caught_looped_back_and_retracted() -> None:
    """The signature test: fabricated numeric + qualitative claims are flagged
    by the verifier, the loop-back fires with feedback, and after iteration
    exhaustion both are retracted and listed — never silently dropped."""
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE, "c2": QUAL_SOURCE}))
    # The synthesizer is stubborn: the revision repeats the same lies.
    synthesis = StructuredFakeModel(outputs=[_lie_synthesis_output(), _lie_synthesis_output()])
    qualitative = StructuredFakeModel(
        default_output=JudgeOutput(
            verdict="unsupported", feedback="the cited excerpt never mentions Vision Pro"
        )
    )
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(
        retriever=retriever, synthesis=synthesis, qualitative=qualitative, critic=critic
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    # The verifier flagged both lies and the loop-back re-researched exactly
    # the failed claims' topics (the two loop-back branches run in parallel,
    # so their relative order is not fixed).
    assert retriever.queries[0] == QUESTION
    assert set(retriever.queries[1:]) == {LIE_NUMERIC, LIE_QUAL}
    assert len(retriever.queries) == 3
    # The feedback was carried back into the revision prompt.
    assert len(synthesis.calls) == 2
    revision_prompt = _human_text(synthesis.calls[1])
    assert "Verification rejected these claims" in revision_prompt
    assert "not found in cited chunk" in revision_prompt  # reconciler feedback
    assert "Vision Pro" in revision_prompt  # judge feedback

    # Iterations exhausted → both lies retracted, the true claim survives.
    assert state.verify_iteration == 2
    statuses = {c.text: c.status for c in state.claims}
    assert statuses[TRUE_NUMERIC] == "grounded"
    assert statuses[LIE_NUMERIC] == "retracted"
    assert statuses[LIE_QUAL] == "retracted"

    # The lies were excised from the answer body and listed in the retracted
    # section (never silently dropped).
    body, _, retracted_section = (state.draft_answer or "").partition(RETRACTED_HEADER)
    assert "391.0 billion" in body
    assert "500.0 billion" not in body
    assert "Vision Pro" not in body
    assert LIE_NUMERIC in retracted_section
    assert LIE_QUAL in retracted_section

    run_result = build_run_result("run-test", state)
    assert len(run_result.grounding) == 1
    assert len(run_result.retracted) == 2
    assert run_result.grounding[0].verdict == "grounded"
    assert run_result.grounding[0].sources[0].accession_no == "test-acc"


async def test_iteration_cap_of_one_skips_loop_back_and_retracts() -> None:
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE, "c2": QUAL_SOURCE}))
    synthesis = StructuredFakeModel(outputs=[_lie_synthesis_output()])
    qualitative = StructuredFakeModel(
        default_output=JudgeOutput(verdict="unsupported", feedback="not in source")
    )
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(
        retriever=retriever,
        synthesis=synthesis,
        qualitative=qualitative,
        critic=critic,
        limits=GraphLimits(
            max_verify_iterations=1, retry_attempts=1, retry_base_delay_seconds=0.01
        ),
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    # No loop-back: research ran once, one verify pass, straight to retract.
    assert retriever.queries == [QUESTION]
    assert state.verify_iteration == 1
    assert len(synthesis.calls) == 1
    assert sum(c.status == "retracted" for c in state.claims) == 2


async def test_all_grounded_single_pass_reaches_critic_and_ends() -> None:
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE, "c2": QUAL_SOURCE}))
    answer = f"{TRUE_NUMERIC} [c1]. Services grew on advertising [c2]."
    synthesis = StructuredFakeModel(
        outputs=[
            SynthesisOutput(
                answer=answer,
                claims=[
                    DraftClaim(text=TRUE_NUMERIC, claim_type="numeric", source_chunk_ids=["c1"]),
                    DraftClaim(
                        text="Services grew on advertising",
                        claim_type="qualitative",
                        source_chunk_ids=["c2"],
                    ),
                ],
            )
        ]
    )
    qualitative = StructuredFakeModel(
        outputs=[JudgeOutput(verdict="grounded", supporting_span=QUAL_SOURCE)]
    )
    critic = StructuredFakeModel(outputs=[CriticOutput(passed=True)])
    graph = make_graph(
        retriever=retriever, synthesis=synthesis, qualitative=qualitative, critic=critic
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert state.draft_answer == answer  # no retraction section
    assert state.verify_iteration == 1
    assert state.critic_iteration == 1
    assert state.critic_passed is True
    assert all(c.status == "grounded" for c in state.claims)
    assert {v.method for v in state.verdicts} == {"numeric_reconciliation", "semantic"}
    assert state.error is None


async def test_verify_routes_each_claim_type_to_its_checker() -> None:
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE}))
    synthesis = StructuredFakeModel(
        outputs=[
            SynthesisOutput(
                answer="a [c1]. b [c1]. c [c1].",
                claims=[
                    DraftClaim(text="numeric claim", claim_type="numeric", source_chunk_ids=["c1"]),
                    DraftClaim(
                        text="qualitative claim", claim_type="qualitative", source_chunk_ids=["c1"]
                    ),
                    DraftClaim(
                        text="forward claim", claim_type="forward_looking", source_chunk_ids=["c1"]
                    ),
                ],
            )
        ]
    )
    reconciler = SpyReconciler(
        ReconcileOutcome(verdict="grounded", supporting_span="NUM", feedback=None)
    )
    qualitative = StructuredFakeModel(
        outputs=[JudgeOutput(verdict="grounded", supporting_span="QUAL")]
    )
    forward = StructuredFakeModel(outputs=[JudgeOutput(verdict="grounded", supporting_span="FWD")])
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(
        retriever=retriever,
        synthesis=synthesis,
        qualitative=qualitative,
        forward=forward,
        critic=critic,
        reconciler=reconciler,
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert reconciler.calls == [("numeric claim", ["c1"])]
    assert len(qualitative.calls) == 1
    assert len(forward.calls) == 1
    by_span = {v.supporting_span: v.method for v in state.verdicts}
    assert by_span == {"NUM": "numeric_reconciliation", "QUAL": "semantic", "FWD": "attribution"}


async def test_claim_without_citation_is_auto_unsupported() -> None:
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE}))
    synthesis = StructuredFakeModel(
        outputs=[
            SynthesisOutput(
                answer="Uncited assertion.",
                claims=[
                    DraftClaim(text="Uncited assertion", claim_type="qualitative"),
                ],
            )
        ]
    )
    qualitative = StructuredFakeModel()  # must never be called
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(
        retriever=retriever,
        synthesis=synthesis,
        qualitative=qualitative,
        critic=critic,
        limits=GraphLimits(
            max_verify_iterations=1, retry_attempts=1, retry_base_delay_seconds=0.01
        ),
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert qualitative.calls == []
    assert state.claims[0].status == "retracted"
    assert "cites no retrievable evidence chunk" in (state.verdicts[0].feedback or "")


async def test_budget_abort_ends_run_with_partial_error_state() -> None:
    retriever = StubRetriever(make_chunks())
    synthesis = StructuredFakeModel()
    tracker = BudgetTracker(max_tokens=10, max_cost_usd=1.0)
    tracker.record(11, 0)  # already over cap before the run starts
    graph = make_graph(retriever=retriever, synthesis=synthesis, tracker=tracker)

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert "budget exceeded" in (state.error or "")
    assert synthesis.calls == []  # no LLM call was made over budget
    assert state.draft_answer is None
    assert state.run_budget.tokens_used == 11


async def test_critic_failure_loops_back_to_synthesize_once() -> None:
    retriever = StubRetriever(make_chunks())
    synthesis = StructuredFakeModel(
        outputs=[
            SynthesisOutput(answer="First draft.", claims=[]),
            SynthesisOutput(answer="Second draft.", claims=[]),
        ]
    )
    critic = StructuredFakeModel(
        outputs=[
            CriticOutput(passed=False, feedback="the question is not actually answered"),
            CriticOutput(passed=True),
        ]
    )
    graph = make_graph(retriever=retriever, synthesis=synthesis, critic=critic)

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert len(synthesis.calls) == 2
    assert "the question is not actually answered" in _human_text(synthesis.calls[1])
    assert state.draft_answer == "Second draft."
    assert state.critic_iteration == 2
    assert state.critic_passed is True


async def test_critic_cycle_is_bounded() -> None:
    retriever = StubRetriever(make_chunks())
    synthesis = StructuredFakeModel(default_output=SynthesisOutput(answer="Draft.", claims=[]))
    critic = StructuredFakeModel(
        default_output=CriticOutput(passed=False, feedback="still not good")
    )
    graph = make_graph(retriever=retriever, synthesis=synthesis, critic=critic)

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    # Terminates at the cap even though the critic never passes.
    assert state.critic_iteration == 2
    assert state.critic_passed is False


async def test_node_timeout_sets_error_state() -> None:
    retriever = StubRetriever(make_chunks())
    synthesis = StructuredFakeModel(
        default_output=SynthesisOutput(answer="x", claims=[]), delay_seconds=0.5
    )
    graph = make_graph(
        retriever=retriever,
        synthesis=synthesis,
        limits=GraphLimits(
            node_timeout_seconds=0.05, retry_attempts=1, retry_base_delay_seconds=0.01
        ),
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert "timed out" in (state.error or "")
    assert state.evidence  # research completed before the timeout hit
    assert state.draft_answer is None
