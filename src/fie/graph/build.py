"""Build the Phase 2 graph: decomposition, parallel research fan-out, fan-in.

    START → decompose ═(Send per open sub-question)═> research (parallel)
                 ↑                                        │
                 │ (critic: mis-split, bounded)           ▼
              critic ←─ retract ←─┬─ verify ←── synthesize (fan-in)
                 │                │    ║
                 ├→ END           │    ╚═(Send per failed claim's
                 └→ synthesize ───┘       sub-question — targeted loop-back)

`decompose` splits the question into independent sub-questions (trivial
questions pass through as a single sub-question, no ceremony). The
conditional edge after it emits one `Send("research", ...)` per open
sub-question; branches run in parallel, bounded by a semaphore, and write
into accumulating reducer channels so their updates merge instead of
clobbering. A failed branch marks its sub-question exhausted instead of
killing the run. `verify` still fans out per claim with `asyncio.gather`;
its loop-back is now targeted — one `Send` per failed claim, re-researching
only that claim's sub-question with the rejection feedback. The critic can
route back to the decomposer (bounded to one re-decomposition per run) when
the answer failed because the question was mis-split. Every node is wrapped
with the timeout/budget guardrails; iteration caps are enforced in the
conditional edges so all three cycles terminate.
"""

import asyncio
import logging
from asyncio import gather
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer, Send

from fie.context import (
    DEFAULT_CHUNKS_PER_SUB_QUESTION,
    build_attribution_messages,
    build_critic_messages,
    build_decompose_messages,
    build_entailment_messages,
    build_query_rewrite_messages,
    build_synthesis_messages,
)
from fie.graph.retract import claim_present, excise_claims, excise_spans
from fie.guardrails import BudgetExceededError, BudgetTracker, ainvoke_with_retry, guarded
from fie.llm import bind_structured
from fie.state import (
    RETRACTED_HEADER,
    BranchResult,
    Claim,
    CriticOutput,
    DecomposeOutput,
    DraftClaim,
    EngineState,
    EvidenceChunk,
    JudgeOutput,
    QueryRewrite,
    ResearchBranch,
    SubQuestion,
    SynthesisOutput,
    Verdict,
    VerifyMethod,
)
from fie.tools.fact_lookup import FactLookup
from fie.tools.reconcile import NumericReconciler
from fie.tools.retrieval import Retriever

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphLimits:
    """Loop bounds and per-node execution limits (guardrails).

    Iteration caps count node *passes*: `max_verify_iterations = 2` means at
    most two verify passes, i.e. one verify→research loop-back; likewise for
    the critic→synthesize cycle. `max_decompose_iterations = 2` allows one
    critic-driven re-decomposition. `max_concurrent_research` bounds the
    fan-out (a semaphore — fan-out multiplies token spend)."""

    max_verify_iterations: int = 2
    max_critic_iterations: int = 2
    max_decompose_iterations: int = 2
    max_sub_questions: int = 5
    max_concurrent_research: int = 5
    node_timeout_seconds: float = 300.0
    retry_attempts: int = 3
    retry_base_delay_seconds: float = 1.0


def build_graph(
    *,
    retriever: Retriever,
    decompose_model: BaseChatModel,
    synthesis_model: BaseChatModel,
    qualitative_model: BaseChatModel,
    forward_model: BaseChatModel,
    critic_model: BaseChatModel,
    rewrite_model: BaseChatModel | None = None,
    reconciler: NumericReconciler,
    tracker: BudgetTracker,
    fact_lookup: FactLookup | None = None,
    limits: GraphLimits | None = None,
    k: int = 6,
    chunks_per_sub_question: int = DEFAULT_CHUNKS_PER_SUB_QUESTION,
    checkpointer: Checkpointer = None,
) -> CompiledStateGraph[EngineState]:
    """Compile the graph over injected dependencies.

    Everything is injected (not constructed here) so tests can run the graph
    with fakes and later phases can swap implementations — Phase 3 replaces
    `reconciler` with the XBRL facts lookup without touching this module.
    """
    limits = limits or GraphLimits()

    decompose_llm = bind_structured(decompose_model, DecomposeOutput)
    synthesis_llm = bind_structured(synthesis_model, SynthesisOutput)
    qualitative_llm = bind_structured(qualitative_model, JudgeOutput)
    forward_llm = bind_structured(forward_model, JudgeOutput)
    critic_llm = bind_structured(critic_model, CriticOutput)
    rewrite_llm = bind_structured(rewrite_model, QueryRewrite) if rewrite_model else None

    # Bounds concurrently-running research branches. One semaphore per
    # compiled graph: a graph instance is bound to one event loop per run
    # (CLI and tests both build per-run graphs).
    research_gate = asyncio.Semaphore(limits.max_concurrent_research)

    async def _call(runnable: Any, value: Any, config: RunnableConfig) -> Any:
        return await ainvoke_with_retry(
            runnable,
            value,
            config,
            tracker=tracker,
            attempts=limits.retry_attempts,
            base_delay_seconds=limits.retry_base_delay_seconds,
        )

    async def decompose(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        """Split the question into independent sub-questions (cheap model).

        Trivial-question passthrough (blueprint §4): a single-part question
        becomes exactly one sub-question carrying the original wording
        verbatim — the decision is visible in the trace as this node's
        output. On a critic-driven re-split, the critic's feedback is in the
        prompt and a fresh generation of sub-question ids replaces the old
        split wholesale."""
        generation = state.decompose_iteration + 1
        messages = build_decompose_messages(
            state.question,
            max_sub_questions=limits.max_sub_questions,
            critic_feedback=state.critic_feedback if generation > 1 else None,
        )
        output = cast(DecomposeOutput, await _call(decompose_llm, messages, config))
        items = output.sub_questions[: limits.max_sub_questions]
        if len(items) <= 1:
            rationale = (
                items[0].rationale if items and items[0].rationale else "single-part question"
            )
            subs = [SubQuestion(id=f"sq{generation}-1", text=state.question, rationale=rationale)]
            logger.info("decompose: single-part question — passthrough")
        else:
            subs = [
                SubQuestion(id=f"sq{generation}-{i + 1}", text=item.text, rationale=item.rationale)
                for i, item in enumerate(items)
            ]
            logger.info("decompose: split into %d sub-questions", len(subs))
        return {
            "sub_questions": subs,
            "decompose_iteration": generation,
            "redecompose_requested": False,
        }

    async def formulate(query: str, config: RunnableConfig) -> str:
        """Restate a branch's query in filing vocabulary before retrieving.

        Not a graph node: it is a per-branch step with no fan-in, so a node
        would need its own channel and change the state machine for nothing.
        One rewrite, never a multi-query fan-out — the ablation says one
        clears the bar, and the call already multiplies across the parallel
        Send fan-out and again on every verify loop-back (ADR 0022). A
        rewrite failure falls back to the analyst's own phrasing rather than
        failing the branch."""
        if rewrite_llm is None:
            return query
        try:
            output = cast(
                QueryRewrite, await _call(rewrite_llm, build_query_rewrite_messages(query), config)
            )
        except (BudgetExceededError, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("query rewrite failed (%s); retrieving with the raw question", exc)
            return query
        return output.query.strip() or query

    async def research(state: ResearchBranch, config: RunnableConfig) -> dict[str, Any]:
        """One fan-out branch: retrieve evidence for its sub-question (or,
        on a targeted loop-back, for the failed claim's text), and look the
        question's filed figures up in the XBRL store. Failures are
        isolated — the branch reports its sub-question exhausted instead of
        killing the run (budget aborts and node timeouts stay global)."""
        sub_question = state.sub_question
        query = state.query or sub_question.text
        try:
            async with research_gate:
                search = await formulate(query, config)
                chunks = await asyncio.to_thread(retriever.retrieve, search, k)
                # The store answers "what is this figure" exactly, where
                # retrieval answers "what text is nearby" — 13 of the 17
                # coverage misses were figures sitting in SQLite that the
                # answering path could not reach (ADR 0022). Injection is
                # additive: a lookup failure or a non-numeric question just
                # yields nothing, and the retrieved prose stands alone.
                facts = (
                    await asyncio.to_thread(fact_lookup.lookup, query)
                    if fact_lookup is not None
                    else []
                )
        except (BudgetExceededError, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("research branch %s failed: %s", sub_question.id, exc)
            return {
                "branch_results": [
                    BranchResult(sub_question_id=sub_question.id, ok=False, detail=str(exc))
                ]
            }
        tagged = [
            c.model_copy(update={"sub_question_id": sub_question.id}) for c in [*facts, *chunks]
        ]
        return {
            "evidence": tagged,
            "branch_results": [
                BranchResult(sub_question_id=sub_question.id, ok=True, query=search)
            ],
        }

    def _stamp_sub_question_statuses(state: EngineState) -> list[SubQuestion]:
        """Fan-in bookkeeping: branches report outcomes on the accumulating
        `branch_results` channel (never writing `sub_questions` concurrently);
        the latest result per sub-question decides its status."""
        latest: dict[str, bool] = {}
        for result in state.branch_results:
            latest[result.sub_question_id] = result.ok
        return [
            sq
            if sq.id not in latest
            else sq.model_copy(update={"status": "researched" if latest[sq.id] else "exhausted"})
            for sq in state.sub_questions
        ]

    def _unquoted(output: SynthesisOutput) -> list[DraftClaim]:
        return [c for c in output.claims if c.answer_span.strip() not in output.answer]

    async def repair_spans(
        output: SynthesisOutput, messages: list[Any], config: RunnableConfig
    ) -> SynthesisOutput:
        """Re-ask once for claims whose span is not verbatim in the answer.

        Models quote imperfectly, so validation is load-bearing — but unlike
        the token-overlap guess it replaces, this failure is *detectable*
        (a substring check) rather than silent, and one bounded retry is the
        whole repair (ADR 0023). A claim still unquoted after it keeps an
        empty span and falls back to token overlap, counted at retract."""
        bad = _unquoted(output)
        if not bad:
            return output
        listing = "\n".join(f"- {claim.text}" for claim in bad)
        logger.info(
            "span repair: %d of %d claims did not quote the answer", len(bad), len(output.claims)
        )
        repair = [
            *messages,
            HumanMessage(
                content=(
                    "These claims' 'answer_span' values do not appear in 'answer' "
                    f"verbatim:\n{listing}\n"
                    "Re-emit the same answer and claims, copying each claim's "
                    "sentence out of 'answer' character for character."
                )
            ),
        ]
        try:
            retried = cast(SynthesisOutput, await _call(synthesis_llm, repair, config))
        except (BudgetExceededError, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("span repair failed (%s); keeping the original draft", exc)
            return output
        if len(_unquoted(retried)) < len(bad):
            return retried
        logger.warning("span repair did not improve quoting; keeping the original draft")
        return output

    async def synthesize(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        """Fan-in: draft (or revise) the answer over evidence merged across
        all sub-questions, as structured output — the prose answer plus the
        claim list, each claim tagged with its cited chunk ids and traced to
        the sub-question whose branch retrieved its evidence."""
        sub_questions = _stamp_sub_question_statuses(state)
        messages = build_synthesis_messages(
            state.question,
            state.evidence,
            sub_questions=sub_questions,
            chunks_per_sub_question=chunks_per_sub_question,
            prior_draft=state.draft_answer,
            rejection_feedback=state.rejection_feedback,
            critic_feedback=state.critic_feedback,
        )
        output = cast(SynthesisOutput, await _call(synthesis_llm, messages, config))
        output = await repair_spans(output, messages, config)
        chunk_owner = {c.chunk_id: c.sub_question_id for c in state.evidence}
        draft_no = state.verify_iteration + state.critic_iteration + 1
        claims = [
            Claim(
                id=f"claim-{draft_no}-{i + 1}",
                text=draft.text,
                claim_type=draft.claim_type,
                source_chunk_ids=draft.source_chunk_ids,
                sub_question_id=next(
                    (
                        chunk_owner[cid]
                        for cid in draft.source_chunk_ids
                        if chunk_owner.get(cid) is not None
                    ),
                    None,
                ),
                answer_span=(
                    draft.answer_span.strip() if draft.answer_span.strip() in output.answer else ""
                ),
            )
            for i, draft in enumerate(output.claims)
        ]
        return {
            "draft_answer": output.answer,
            "claims": claims,
            "critic_feedback": None,
            "sub_questions": sub_questions,
        }

    async def verify_claim(
        claim: Claim, chunk_map: dict[str, EvidenceChunk], config: RunnableConfig
    ) -> Verdict:
        """Verify one claim against its cited chunks, routed by claim type."""
        cited = [chunk_map[cid] for cid in claim.source_chunk_ids if cid in chunk_map]
        if not cited:
            return Verdict(
                claim_id=claim.id,
                verdict="unsupported",
                method="attribution",
                feedback="claim cites no retrievable evidence chunk — cite a real chunk or retract",
            )
        if claim.claim_type == "numeric":
            # Phase 3 reconciler does SQLite + mapping-model I/O — keep it
            # off the event loop (the protocol stays sync, per ADR 0005).
            outcome = await asyncio.to_thread(reconciler.reconcile, claim.text, cited)
            return Verdict(
                claim_id=claim.id,
                verdict=outcome.verdict,
                supporting_span=outcome.supporting_span,
                method="numeric_reconciliation",
                feedback=outcome.feedback,
            )
        method: VerifyMethod
        if claim.claim_type == "qualitative":
            messages = build_entailment_messages(claim.text, cited)
            ruling = cast(JudgeOutput, await _call(qualitative_llm, messages, config))
            method = "semantic"
        else:
            messages = build_attribution_messages(claim.text, cited)
            ruling = cast(JudgeOutput, await _call(forward_llm, messages, config))
            method = "attribution"
        return Verdict(
            claim_id=claim.id,
            verdict=ruling.verdict,
            supporting_span=ruling.supporting_span,
            method=method,
            feedback=ruling.feedback,
        )

    async def verify(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        """Fan out per claim (asyncio.gather) and collect verdicts."""
        chunk_map = {c.chunk_id: c for c in state.evidence}
        verdicts = list(
            await gather(*(verify_claim(claim, chunk_map, config) for claim in state.claims))
        )
        claims = [
            claim.model_copy(update={"status": verdict.verdict})
            for claim, verdict in zip(state.claims, verdicts, strict=True)
        ]
        feedback = [
            f"claim {claim.id} ({claim.text!r}): "
            f"{verdict.feedback or 'not supported by the cited source'}"
            for claim, verdict in zip(claims, verdicts, strict=True)
            if verdict.verdict == "unsupported"
        ]
        logger.info(
            "verify pass %d: %d claim(s), %d unsupported",
            state.verify_iteration + 1,
            len(claims),
            len(feedback),
        )
        return {
            "verdicts": verdicts,
            "claims": claims,
            "verify_iteration": state.verify_iteration + 1,
            "rejection_feedback": feedback,
        }

    async def retract(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        """Deterministically excise still-unsupported claims from the answer
        and list them in a retracted-claims section — never silently drop.

        Whether a claim left the answer is decided by comparing the draft
        before and after one global excision pass, not by whether this
        claim's own excision call reported success. Claims overlap, so a
        claim's sentence is routinely removed by a *different* claim's
        excision; reading that as "could not locate" mislabelled four real
        retractions as surviving hallucinations and, worse, left three
        verified claims advertised in the grounding table for prose the
        reader never saw (ADR 0021)."""
        del config
        unsupported = [c for c in state.claims if c.status == "unsupported"]
        if not unsupported:
            return {}
        draft = state.draft_answer or ""
        # Spans first — exact, order-independent, no collateral. Only claims
        # the synthesizer would not quote reach the token-overlap assignment.
        body = excise_spans(draft, [c.answer_span for c in unsupported if c.answer_span])
        guessed = [c for c in unsupported if not c.answer_span]
        if guessed:
            logger.warning(
                "%d of %d unsupported claim(s) carry no answer span; "
                "falling back to token-overlap excision",
                len(guessed),
                len(unsupported),
            )
            body = excise_claims(body, [c.text for c in guessed])

        claims: list[Claim] = []
        excised: list[Claim] = []
        stranded: list[Claim] = []
        for claim in state.claims:
            was_present = claim_present(draft, claim.text, claim.answer_span)
            survives = claim_present(body, claim.text, claim.answer_span)
            if claim.status == "unsupported":
                if was_present and not survives:
                    excised.append(claim)
                    claims.append(claim.model_copy(update={"status": "retracted"}))
                else:
                    # Its text is still in the prose (or was never locatable
                    # there): NOT a successful retraction. Keeping it
                    # `unsupported` keeps it in the grounding rows and against
                    # hallucination_rate — marking it "retracted" would hide
                    # surviving unsupported text from the exit-gate metric.
                    logger.warning(
                        "could not locate claim %s in draft for excision; "
                        "text remains in the answer and still counts as unsupported",
                        claim.id,
                    )
                    stranded.append(claim)
                    claims.append(claim)
            elif was_present and not survives:
                # A verified claim whose sentence another claim's excision
                # took. Citing a source for prose the user never saw is the
                # precise failure this system exists to prevent, so it leaves
                # the grounding table rather than being advertised as
                # delivered. Detected and corrected here, asserted in tests —
                # a hard assertion in the graph would have killed the accepted
                # run over three claims in 275.
                logger.warning(
                    "claim %s (%s) lost its sentence to another claim's excision — "
                    "dropping it from the grounding table, it was not delivered",
                    claim.id,
                    claim.status,
                )
            else:
                claims.append(claim)
        draft = body

        sections = []
        if excised:
            listing = "\n".join(f"- {claim.text}" for claim in excised)
            sections.append(
                "The following claims could not be verified against their cited "
                f"sources and were removed from the answer:\n{listing}"
            )
        if stranded:
            listing = "\n".join(f"- {claim.text}" for claim in stranded)
            sections.append(
                "The following claims could not be verified against their cited "
                "sources. They could not be located for removal, so they may "
                f"still appear above — treat them as unverified:\n{listing}"
            )
        draft = f"{draft.rstrip()}\n\n{RETRACTED_HEADER}\n\n" + "\n\n".join(sections)
        logger.info(
            "retraction after iteration exhaustion: %d excised, %d stranded in draft",
            len(excised),
            len(stranded),
        )
        return {"draft_answer": draft, "claims": claims}

    async def critic(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        """Rubric quality gate on the surviving draft."""
        messages = build_critic_messages(state.question, state.draft_answer or "", state.claims)
        ruling = cast(CriticOutput, await _call(critic_llm, messages, config))
        return {
            "critic_passed": ruling.passed,
            "critic_feedback": ruling.feedback,
            "critic_iteration": state.critic_iteration + 1,
            "redecompose_requested": ruling.re_decompose,
        }

    def fan_out_research(state: EngineState) -> list[Send] | str:
        """After decompose: one Send per open sub-question — the parallel
        research fan-out."""
        if state.error is not None:
            return END
        sends = [
            Send("research", ResearchBranch(sub_question=sq))
            for sq in state.sub_questions
            if sq.status == "open"
        ]
        if not sends:
            return END
        return sends

    def after_verify(state: EngineState) -> list[Send] | str:
        """The loop-back decision (conditional edge). A loop-back is now
        targeted: one Send per failed claim, re-researching only that
        claim's sub-question with the rejection feedback in the branch."""
        if state.error is not None:
            return END
        unsupported = [c for c in state.claims if c.status == "unsupported"]
        if not unsupported:
            return "critic"
        if state.verify_iteration < limits.max_verify_iterations:
            logger.info(
                "loop-back %d: %d unsupported claim(s) → research",
                state.verify_iteration,
                len(unsupported),
            )
            sub_map = {sq.id: sq for sq in state.sub_questions}
            verdict_map = {v.claim_id: v for v in state.verdicts}
            sends: list[Send] = []
            for claim in unsupported:
                sub_question = sub_map.get(claim.sub_question_id or "")
                if sub_question is None:
                    # Uncited or unmapped claim: research it under an ad-hoc
                    # branch so the retrieval still happens and stays traced.
                    sub_question = SubQuestion(
                        id=f"sq-adhoc-{claim.id}", text=claim.text, status="researched"
                    )
                verdict = verdict_map.get(claim.id)
                sends.append(
                    Send(
                        "research",
                        ResearchBranch(
                            sub_question=sub_question,
                            query=claim.text,
                            feedback=verdict.feedback if verdict else None,
                        ),
                    )
                )
            return sends
        return "retract"

    def after_critic(state: EngineState) -> str:
        """The critic gate decision (conditional edge, bounded). A rubric
        failure attributed to mis-decomposition re-enters the decomposer
        (at most one re-decomposition per run); other failures revise."""
        if state.error is not None:
            return END
        if state.critic_passed:
            return END
        if state.critic_iteration >= limits.max_critic_iterations:
            return END
        if state.redecompose_requested and state.decompose_iteration < (
            limits.max_decompose_iterations
        ):
            logger.info("critic failed (pass %d): mis-split → decompose", state.critic_iteration)
            return "decompose"
        logger.info("critic failed (pass %d) → synthesize", state.critic_iteration)
        return "synthesize"

    graph: StateGraph[EngineState] = StateGraph(EngineState)
    for name, fn in (
        ("decompose", decompose),
        ("synthesize", synthesize),
        ("verify", verify),
        ("retract", retract),
        ("critic", critic),
    ):
        graph.add_node(
            name, guarded(name, fn, tracker=tracker, timeout_seconds=limits.node_timeout_seconds)
        )
    graph.add_node(
        "research",
        guarded("research", research, tracker=tracker, timeout_seconds=limits.node_timeout_seconds),
        input_schema=ResearchBranch,
    )
    graph.add_edge(START, "decompose")
    graph.add_conditional_edges("decompose", fan_out_research, ["research", END])
    graph.add_edge("research", "synthesize")
    graph.add_edge("synthesize", "verify")
    graph.add_conditional_edges("verify", after_verify, ["research", "retract", "critic", END])
    graph.add_edge("retract", "critic")
    graph.add_conditional_edges("critic", after_critic, ["synthesize", "decompose", END])
    return graph.compile(checkpointer=checkpointer)
