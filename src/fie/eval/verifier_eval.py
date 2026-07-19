"""Verifier P/R measurement (workstream C.3, decision 3.5).

Runs the *same building blocks the graph uses* — the numeric reconciler
for numeric claims, the entailment judge for qualitative claims — over the
labeled claim set, and scores the verdicts per claim type. This is the
measurement that decides verifier model escalation (cheap vs frontier per
claim type): if a claim type's precision is below target on the cheap
judge, that type escalates.
"""

import logging
from typing import Any

from fie.context import build_entailment_messages
from fie.eval.datasets import LabeledClaim
from fie.eval.metrics import VerifierScore, verifier_scores
from fie.state import EvidenceChunk, JudgeOutput, VerdictLabel
from fie.tools.reconcile import NumericReconciler

logger = logging.getLogger(__name__)


def _evidence_chunk(claim: LabeledClaim) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=f"ev-{claim.id}",
        doc_id=f"{claim.ticker}-eval",
        ticker=claim.ticker or "TSLA",
        form_type="10-K",
        section="Evidence",
        text=claim.evidence_text,
        metadata={"chunk_type": "text"},
    )


def run_verifier_eval(
    labeled: list[LabeledClaim],
    *,
    reconciler: NumericReconciler,
    qualitative_judge: Any,
) -> tuple[dict[str, VerdictLabel], list[VerifierScore]]:
    """Predict a verdict for every labeled claim and score P/R per type.

    `qualitative_judge` is a structured-output runnable emitting
    `JudgeOutput` (the graph's entailment judge, bound the same way).
    """
    predictions: dict[str, VerdictLabel] = {}
    for claim in labeled:
        if claim.claim_type == "numeric":
            outcome = reconciler.reconcile(claim.text, [_evidence_chunk(claim)])
            predictions[claim.id] = outcome.verdict
        else:
            messages = build_entailment_messages(claim.text, [_evidence_chunk(claim)])
            try:
                ruling = qualitative_judge.invoke(messages)
                output = (
                    ruling
                    if isinstance(ruling, JudgeOutput)
                    else JudgeOutput.model_validate(ruling)
                )
                predictions[claim.id] = output.verdict
            except Exception as exc:
                logger.warning("judge failed on %s: %s", claim.id, exc)
                predictions[claim.id] = "unsupported"
    return predictions, verifier_scores(labeled, predictions)
