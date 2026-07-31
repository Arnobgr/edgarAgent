"""Phase 1 graph behavior with fakes — no network.

The planted-lie test is the phase's signature test: a fabricated figure and a
fabricated qualitative claim must be flagged, loop back with feedback, and be
retracted (never silently dropped) once iterations are exhausted.
"""

from typing import Any

from fie.eval.metrics import hallucination_rate
from fie.graph import GraphLimits
from fie.graph.build import RETRACTED_HEADER
from fie.graph.retract import MIN_OVERLAP, best_overlap
from fie.state import (
    CriticOutput,
    DraftClaim,
    EngineState,
    JudgeOutput,
    QueryRewrite,
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


REWRITTEN = "Tesla consolidated statements of operations total revenues fiscal year 2025"


def _grounded_output(answer: str) -> SynthesisOutput:
    return SynthesisOutput(
        answer=answer,
        claims=[DraftClaim(text=TRUE_NUMERIC, claim_type="numeric", source_chunk_ids=["c1"])],
    )


async def test_research_retrieves_with_the_rewritten_query_and_records_it() -> None:
    """`research` retrieved with `state.query or sub_question.text` — the raw
    analyst phrasing — and no query-formulation stage existed anywhere in the
    system. Filing vocabulary moved 10 of 11 previously-failing figures into
    the top 10 (ADR 0022). The query used is recorded so a retrieval failure
    stays distinguishable from a rewrite failure."""
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE}))
    rewrite = StructuredFakeModel(default_output=QueryRewrite(query=REWRITTEN))
    synthesis = StructuredFakeModel(outputs=[_grounded_output(f"{TRUE_NUMERIC} [c1].")])
    critic = StructuredFakeModel(outputs=[CriticOutput(passed=True)])
    graph = make_graph(retriever=retriever, synthesis=synthesis, critic=critic, rewrite=rewrite)

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert retriever.queries == [REWRITTEN]
    assert [b.query for b in state.branch_results] == [REWRITTEN]


async def test_a_failed_rewrite_falls_back_to_the_analyst_phrasing() -> None:
    """The rewrite is an improvement, not a dependency: losing it must cost
    ranking, never the branch."""
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE}))
    rewrite = StructuredFakeModel(default_output=QueryRewrite(query=REWRITTEN), fail_times=99)
    synthesis = StructuredFakeModel(outputs=[_grounded_output(f"{TRUE_NUMERIC} [c1].")])
    critic = StructuredFakeModel(outputs=[CriticOutput(passed=True)])
    graph = make_graph(retriever=retriever, synthesis=synthesis, critic=critic, rewrite=rewrite)

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert retriever.queries == [QUESTION]
    assert state.error is None
    assert state.draft_answer is not None


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


# Two claims restated from one sentence, the way a real draft produces them:
# "Data Center revenue was $X billion, up Y% year-on-year" yields both an
# amount claim and a growth claim over the same prose.
SHARED_SENTENCE = "Vision Pro revenue was $500.0 billion, up 41% year-on-year [c2]."
SHARED_AMOUNT = "Vision Pro revenue was $500.0 billion"
SHARED_GROWTH = "Vision Pro revenue was up 41% year-on-year"


def _shared_sentence_output() -> SynthesisOutput:
    return SynthesisOutput(
        answer=f"{TRUE_NUMERIC} [c1]. {SHARED_SENTENCE}",
        claims=[
            DraftClaim(text=TRUE_NUMERIC, claim_type="numeric", source_chunk_ids=["c1"]),
            DraftClaim(text=SHARED_AMOUNT, claim_type="numeric", source_chunk_ids=["c2"]),
            DraftClaim(text=SHARED_GROWTH, claim_type="numeric", source_chunk_ids=["c2"]),
        ],
    )


async def test_claims_sharing_a_sentence_are_both_retracted_by_one_deletion() -> None:
    """Overlapping claims used to delete each other's sentences: excision
    re-derived its target against a draft that mutated as it went, so the
    second claim found its sentence already gone and was logged "could not
    locate" — reported as a surviving hallucination when its text had in fact
    left the answer (ADR 0021). One sentence, one deletion, both retracted."""
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE, "c2": QUAL_SOURCE}))
    synthesis = StructuredFakeModel(outputs=[_shared_sentence_output(), _shared_sentence_output()])
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(retriever=retriever, synthesis=synthesis, critic=critic)

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    statuses = {c.text: c.status for c in state.claims}
    assert statuses[SHARED_AMOUNT] == "retracted"
    assert statuses[SHARED_GROWTH] == "retracted"

    body, _, retracted_section = (state.draft_answer or "").partition(RETRACTED_HEADER)
    assert "Vision Pro" not in body
    assert "still appear above" not in retracted_section  # nothing was stranded

    run_result = build_run_result("run-shared", state)
    assert hallucination_rate([run_result]) == 0.0


GROUNDED_NEIGHBOUR = "Vision Pro revenue reached a record in the fourth quarter"


def _collateral_output() -> SynthesisOutput:
    """A verified claim and an unsupported one restated from the same
    sentence — the shape that silently deleted verified prose."""
    return SynthesisOutput(
        answer=f"{TRUE_NUMERIC} [c1]. {SHARED_SENTENCE}",
        claims=[
            DraftClaim(text=TRUE_NUMERIC, claim_type="numeric", source_chunk_ids=["c1"]),
            DraftClaim(text=SHARED_AMOUNT, claim_type="numeric", source_chunk_ids=["c2"]),
            DraftClaim(text=SHARED_GROWTH, claim_type="qualitative", source_chunk_ids=["c2"]),
        ],
    )


async def test_a_verified_claim_that_lost_its_sentence_leaves_the_grounding_table() -> None:
    """Three `grounded` claims in the accepted run lost their home sentence to
    another claim's excision and stayed listed as delivered, verified claims.
    Citing a source for prose the reader never saw is the precise failure this
    system exists to prevent, so the claim is dropped and logged (ADR 0021).

    The invariant, asserted absolutely here and only corrected in the graph:
    every surviving grounded/partial claim's sentence is still present, and
    every retracted claim's is gone."""
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE, "c2": QUAL_SOURCE}))
    synthesis = StructuredFakeModel(outputs=[_collateral_output(), _collateral_output()])
    # The qualitative sibling verifies fine — it is the collateral victim.
    qualitative = StructuredFakeModel(
        default_output=JudgeOutput(verdict="grounded", supporting_span="span")
    )
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(
        retriever=retriever, synthesis=synthesis, qualitative=qualitative, critic=critic
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)
    run_result = build_run_result("run-collateral", state)

    body = (state.draft_answer or "").partition(RETRACTED_HEADER)[0]
    assert "Vision Pro" not in body  # the shared sentence is gone
    delivered = {row.text for row in run_result.grounding}
    assert SHARED_GROWTH not in delivered, (
        "a verified claim whose sentence was deleted must not be advertised as delivered"
    )
    for row in run_result.grounding:
        assert best_overlap(body, row.text) >= MIN_OVERLAP, (
            f"grounding row {row.claim_id} cites prose that is not in the answer"
        )
    for row in run_result.retracted:
        assert best_overlap(body, row.text) < MIN_OVERLAP


SPAN_ANSWER = f"{TRUE_NUMERIC} [c1]. {SHARED_SENTENCE}"


def _spanned_output(*, quote: bool) -> SynthesisOutput:
    """The same draft, with the claims either quoting their sentence or not."""

    def span(text: str) -> str:
        return text if quote else "a sentence that is not in the answer"

    return SynthesisOutput(
        answer=SPAN_ANSWER,
        claims=[
            DraftClaim(
                text=TRUE_NUMERIC,
                claim_type="numeric",
                source_chunk_ids=["c1"],
                answer_span=span(f"{TRUE_NUMERIC} [c1]."),
            ),
            DraftClaim(
                text=SHARED_AMOUNT,
                claim_type="numeric",
                source_chunk_ids=["c2"],
                answer_span=span(SHARED_SENTENCE),
            ),
            DraftClaim(
                text=SHARED_GROWTH,
                claim_type="qualitative",
                source_chunk_ids=["c2"],
                answer_span=span(SHARED_SENTENCE),
            ),
        ],
    )


async def test_quoted_spans_make_excision_exact() -> None:
    """The synthesizer wrote the prose and the claim list and knows which
    sentence states which claim; re-deriving that by token overlap is what
    deleted the wrong sentences (ADR 0023). With spans, the sentence two
    claims share is removed once and the verified claim beside it is
    untouched — the Stage B invariant holds by construction."""
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE, "c2": QUAL_SOURCE}))
    synthesis = StructuredFakeModel(
        outputs=[_spanned_output(quote=True), _spanned_output(quote=True)]
    )
    qualitative = StructuredFakeModel(
        default_output=JudgeOutput(verdict="grounded", supporting_span="span")
    )
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(
        retriever=retriever, synthesis=synthesis, qualitative=qualitative, critic=critic
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)
    run_result = build_run_result("run-span", state)

    assert all(c.answer_span in SPAN_ANSWER for c in state.claims if c.answer_span)
    body = (state.draft_answer or "").partition(RETRACTED_HEADER)[0]
    assert TRUE_NUMERIC in body
    assert "Vision Pro" not in body
    # The verified claim that shared the removed sentence is not advertised.
    assert {row.text for row in run_result.grounding} == {TRUE_NUMERIC}


async def test_a_non_quoting_model_triggers_one_repair() -> None:
    """Models quote imperfectly, so the substring check is load-bearing —
    but unlike the guess it replaces, the failure is detectable and one
    bounded retry is the whole repair (ADR 0023)."""
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE, "c2": QUAL_SOURCE}))
    synthesis = StructuredFakeModel(
        outputs=[
            _spanned_output(quote=False),  # the draft, quoting nothing
            _spanned_output(quote=True),  # the repair
            _spanned_output(quote=True),  # the loop-back revision
        ]
    )
    qualitative = StructuredFakeModel(
        default_output=JudgeOutput(verdict="grounded", supporting_span="span")
    )
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(
        retriever=retriever, synthesis=synthesis, qualitative=qualitative, critic=critic
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    assert "do not appear in 'answer'" in _human_text(synthesis.calls[1])
    assert all(c.answer_span for c in state.claims)


STRANDED_CLAIM = "Apple's Vision Pro segment expanded substantially during the fiscal period"


def _unexcisable_synthesis_output() -> SynthesisOutput:
    """A claim restated so self-containedly that it shares almost no tokens
    with its sentence in the prose — `excise_claim` cannot locate it."""
    return SynthesisOutput(
        answer=f"{TRUE_NUMERIC} [c1]. It grew a lot [c2].",
        claims=[
            DraftClaim(text=TRUE_NUMERIC, claim_type="numeric", source_chunk_ids=["c1"]),
            DraftClaim(text=STRANDED_CLAIM, claim_type="qualitative", source_chunk_ids=["c2"]),
        ],
    )


async def test_unexcisable_claim_stays_unsupported_and_counts_as_hallucination() -> None:
    """A claim whose sentence cannot be located is NOT a successful retraction:
    its text survives in the prose, so it must stay `unsupported` and keep
    counting against hallucination_rate. Marking it `retracted` would route it
    out of the grounding rows and hide surviving unsupported text from the
    exit-gate metric."""
    retriever = StubRetriever(make_chunks({"c1": NUMERIC_SOURCE, "c2": QUAL_SOURCE}))
    synthesis = StructuredFakeModel(
        outputs=[_unexcisable_synthesis_output(), _unexcisable_synthesis_output()]
    )
    qualitative = StructuredFakeModel(
        default_output=JudgeOutput(verdict="unsupported", feedback="not in source")
    )
    critic = StructuredFakeModel(default_output=CriticOutput(passed=True))
    graph = make_graph(
        retriever=retriever, synthesis=synthesis, qualitative=qualitative, critic=critic
    )

    result = await graph.ainvoke(EngineState(question=QUESTION))
    state = EngineState.model_validate(result)

    statuses = {c.text: c.status for c in state.claims}
    assert statuses[STRANDED_CLAIM] == "unsupported", (
        "a claim that could not be excised must not be reported as retracted"
    )

    # The unsupported text really is still in the answer body.
    body, _, retracted_section = (state.draft_answer or "").partition(RETRACTED_HEADER)
    assert "It grew a lot" in body
    assert "may" in retracted_section and "still appear above" in retracted_section

    # It stays in the grounding rows, so the gate metric can see it.
    run_result = build_run_result("run-stranded", state)
    assert not run_result.retracted
    assert any(row.verdict == "unsupported" for row in run_result.grounding)
    assert hallucination_rate([run_result]) > 0.0
