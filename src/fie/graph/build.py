"""Build the Phase 1 graph: the cyclic core.

    START → research → synthesize → verify ─┬→ research   (loop-back, bounded)
                                            ├→ retract → critic ─┬→ END
                                            ├→ critic ───────────┼→ END
                                            └→ END (error)       └→ synthesize (bounded)

`verify` fans out per claim with `asyncio.gather` and routes internally by
claim type (numeric → reconciler tool, qualitative → entailment judge,
forward-looking → attribution judge). Verification re-runs in full on every
redraft — a claim can only reach the output by re-passing grounding, never
on the synthesizer's compliance. Retraction is a deterministic code step.
Every node is wrapped with the timeout/budget guardrails; iteration caps are
enforced in the conditional edges so both cycles terminate.
"""

import logging
from asyncio import gather
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from fie.context import (
    build_attribution_messages,
    build_critic_messages,
    build_entailment_messages,
    build_synthesis_messages,
)
from fie.graph.retract import excise_claim
from fie.guardrails import BudgetTracker, ainvoke_with_retry, guarded
from fie.state import (
    Claim,
    CriticOutput,
    EngineState,
    EvidenceChunk,
    JudgeOutput,
    SynthesisOutput,
    Verdict,
    VerifyMethod,
)
from fie.tools.reconcile import NumericReconciler
from fie.tools.retrieval import Retriever

logger = logging.getLogger(__name__)

RETRACTED_HEADER = "### Retracted claims"


@dataclass(frozen=True)
class GraphLimits:
    """Loop bounds and per-node execution limits (guardrails).

    Iteration caps count node *passes*: `max_verify_iterations = 2` means at
    most two verify passes, i.e. one verify→research loop-back; likewise for
    the critic→synthesize cycle."""

    max_verify_iterations: int = 2
    max_critic_iterations: int = 2
    node_timeout_seconds: float = 300.0
    retry_attempts: int = 3
    retry_base_delay_seconds: float = 1.0


def build_graph(
    *,
    retriever: Retriever,
    synthesis_model: BaseChatModel,
    qualitative_model: BaseChatModel,
    forward_model: BaseChatModel,
    critic_model: BaseChatModel,
    reconciler: NumericReconciler,
    tracker: BudgetTracker,
    limits: GraphLimits | None = None,
    k: int = 6,
    checkpointer: Checkpointer = None,
) -> CompiledStateGraph[EngineState]:
    """Compile the graph over injected dependencies.

    Everything is injected (not constructed here) so tests can run the graph
    with fakes and later phases can swap implementations — Phase 3 replaces
    `reconciler` with the XBRL facts lookup without touching this module.
    """
    limits = limits or GraphLimits()

    synthesis_llm = synthesis_model.with_structured_output(SynthesisOutput)
    qualitative_llm = qualitative_model.with_structured_output(JudgeOutput)
    forward_llm = forward_model.with_structured_output(JudgeOutput)
    critic_llm = critic_model.with_structured_output(CriticOutput)

    async def _call(runnable: Any, value: Any, config: RunnableConfig) -> Any:
        return await ainvoke_with_retry(
            runnable,
            value,
            config,
            tracker=tracker,
            attempts=limits.retry_attempts,
            base_delay_seconds=limits.retry_base_delay_seconds,
        )

    async def research(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        """Retrieve evidence: for the question initially; on a loop-back, only
        for the failed claims' topics, merged into the existing evidence."""
        del config
        failed = [c for c in state.claims if c.status == "unsupported"]
        if not failed:
            return {"evidence": retriever.retrieve(state.question, k=k)}
        merged = {c.chunk_id: c for c in state.evidence}
        for claim in failed:
            for chunk in retriever.retrieve(claim.text, k=k):
                merged.setdefault(chunk.chunk_id, chunk)
        logger.info("research loop-back: re-retrieved for %d failed claim(s)", len(failed))
        return {"evidence": list(merged.values())}

    async def synthesize(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        """Draft (or revise) the answer as structured output: the prose answer
        plus the claim list, each claim tagged with its cited chunk ids."""
        messages = build_synthesis_messages(
            state.question,
            state.evidence,
            prior_draft=state.draft_answer,
            rejection_feedback=state.rejection_feedback,
            critic_feedback=state.critic_feedback,
        )
        output = cast(SynthesisOutput, await _call(synthesis_llm, messages, config))
        draft_no = state.verify_iteration + state.critic_iteration + 1
        claims = [
            Claim(
                id=f"claim-{draft_no}-{i + 1}",
                text=draft.text,
                claim_type=draft.claim_type,
                source_chunk_ids=draft.source_chunk_ids,
            )
            for i, draft in enumerate(output.claims)
        ]
        return {"draft_answer": output.answer, "claims": claims, "critic_feedback": None}

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
            outcome = reconciler.reconcile(claim.text, cited)
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
        and list them in a retracted-claims section — never silently drop."""
        del config
        unsupported = [c for c in state.claims if c.status == "unsupported"]
        if not unsupported:
            return {}
        draft = state.draft_answer or ""
        claims: list[Claim] = []
        for claim in state.claims:
            if claim.status != "unsupported":
                claims.append(claim)
                continue
            draft, removed = excise_claim(draft, claim.text)
            if not removed:
                logger.warning("could not locate claim %s in draft for excision", claim.id)
            claims.append(claim.model_copy(update={"status": "retracted"}))
        listing = "\n".join(f"- {claim.text}" for claim in unsupported)
        draft = (
            f"{draft.rstrip()}\n\n{RETRACTED_HEADER}\n\n"
            "The following claims could not be verified against their cited "
            f"sources and were removed from the answer:\n{listing}"
        )
        logger.info("retracted %d claim(s) after iteration exhaustion", len(unsupported))
        return {"draft_answer": draft, "claims": claims}

    async def critic(state: EngineState, config: RunnableConfig) -> dict[str, Any]:
        """Rubric quality gate on the surviving draft."""
        messages = build_critic_messages(state.question, state.draft_answer or "", state.claims)
        ruling = cast(CriticOutput, await _call(critic_llm, messages, config))
        return {
            "critic_passed": ruling.passed,
            "critic_feedback": ruling.feedback,
            "critic_iteration": state.critic_iteration + 1,
        }

    def after_verify(state: EngineState) -> str:
        """The loop-back decision (conditional edge)."""
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
            return "research"
        return "retract"

    def after_critic(state: EngineState) -> str:
        """The critic gate decision (conditional edge, bounded)."""
        if state.error is not None:
            return END
        if state.critic_passed or state.critic_iteration >= limits.max_critic_iterations:
            return END
        logger.info("critic failed (pass %d) → synthesize", state.critic_iteration)
        return "synthesize"

    graph: StateGraph[EngineState] = StateGraph(EngineState)
    for name, fn in (
        ("research", research),
        ("synthesize", synthesize),
        ("verify", verify),
        ("retract", retract),
        ("critic", critic),
    ):
        graph.add_node(
            name, guarded(name, fn, tracker=tracker, timeout_seconds=limits.node_timeout_seconds)
        )
    graph.add_edge(START, "research")
    graph.add_edge("research", "synthesize")
    graph.add_edge("synthesize", "verify")
    graph.add_conditional_edges(
        "verify",
        after_verify,
        {"research": "research", "retract": "retract", "critic": "critic", END: END},
    )
    graph.add_edge("retract", "critic")
    graph.add_conditional_edges("critic", after_critic, {"synthesize": "synthesize", END: END})
    return graph.compile(checkpointer=checkpointer)
