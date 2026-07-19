"""Context management: what goes into each LLM call's window.

Packing decisions (which chunks, how truncated, how formatted) live here —
never inside graph nodes (blueprint §3: the separation is the point).
"""

from collections.abc import Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from fie.state import Claim, EvidenceChunk, SubQuestion

# Cap on evidence text per chunk when packed into the window. Generous for
# the single-filing corpus; retrieval-as-context does the real work later.
MAX_CHARS_PER_CHUNK = 6000

# Fan-in chunk budget per sub-question: n branches x k chunks can exceed what
# synthesis should see, so each sub-question's evidence is capped (best-scored
# first). Measured properly in Phase 3.
DEFAULT_CHUNKS_PER_SUB_QUESTION = 8

DECOMPOSE_SYSTEM_PROMPT = """\
You split an analyst question about a company's SEC filings into independent \
research sub-questions.

Produce structured output: "sub_questions", a list where each entry has:
- "text": one self-contained sub-question, answerable on its own from \
filings evidence.
- "rationale": one sentence on why this is a separate line of research.

Rules:
- Split only a genuinely multi-part question (e.g. asking about margins AND \
capital allocation AND risks). Each sub-question must be independently \
researchable — never sequential steps of one lookup.
- If the question is single-part, return an empty list (or a single entry): \
it will be researched as-is, without ceremony.
- Never invent parts the question does not ask about.
"""

SYNTHESIS_SYSTEM_PROMPT = """\
You are a financial analyst answering questions about a company using only \
the evidence excerpts provided from its SEC filings.

Produce structured output with two fields:
1. "answer" — the prose answer. Cite the supporting chunk id inline after \
every figure and material claim, like [c3].
2. "claims" — every discrete factual claim made in the answer. For each:
   - "text": the claim restated as one self-contained sentence, staying \
close to the answer's wording.
   - "claim_type": "numeric" if it asserts a specific figure or quantity; \
"forward_looking" if it reports guidance, expectations, or projections; \
otherwise "qualitative".
   - "source_chunk_ids": the ids of the evidence chunks that support it. \
A claim without a citation will be rejected by verification.

Rules:
- Base every factual claim strictly on the evidence provided. If the \
evidence does not support an answer, say so explicitly.
- Be precise with numbers, fiscal periods, and segment names exactly as \
they appear in the evidence.
- When revising a rejected draft: for each rejected claim, either re-ground \
it in the evidence with a correct citation or remove it entirely — never \
keep a claim you cannot cite.
"""

ENTAILMENT_SYSTEM_PROMPT = """\
You are a strict verification judge. Given one claim and the source \
excerpts it cites, decide whether the excerpts support the claim.

- "grounded": the excerpts fully support the claim. Quote the exact \
supporting sentence(s) verbatim in "supporting_span".
- "partial": the excerpts support the claim only with material caveats or \
approximations. Quote the closest span and explain the gap in "feedback".
- "unsupported": the excerpts do not support (or they contradict) the \
claim. Explain in "feedback" exactly what is missing or contradicted — \
this feedback drives re-retrieval.

Judge only against the provided excerpts. Outside knowledge must never \
make a claim grounded.
"""

ATTRIBUTION_SYSTEM_PROMPT = """\
You are a verification judge for forward-looking statements. Given one \
claim that reports guidance, expectations, or projections, and the source \
excerpts it cites, check attribution: the excerpts must actually contain \
that forward-looking statement, attributed as such (e.g. company guidance \
or management expectations).

- "grounded": an excerpt contains the forward-looking statement, attributed \
as such. Quote it verbatim in "supporting_span".
- "partial": an excerpt contains a related but materially different \
statement. Quote the closest span and explain the gap in "feedback".
- "unsupported": the excerpts contain no such statement, or the claim \
presents a projection as established fact. Explain in "feedback".
"""

CRITIC_SYSTEM_PROMPT = """\
You are the final quality gate for an evidence-grounded answer to an \
analyst question. You receive the question, the draft answer, and the \
claim list with verification statuses. Check:
1. The question is actually answered.
2. Every surviving (non-retracted) claim is verified grounded or partial.
3. The answer contains no material factual statements beyond the listed, \
cited claims.

A "Retracted claims" section at the end of the answer is expected when \
verification removed claims — do not fail for it. Set "passed" to false \
only for a genuine failure, with specific, actionable "feedback" for the \
reviser; otherwise set "passed" to true.

When failing, also set "re_decompose": true only if the failure stems from \
the question having been split into the wrong sub-questions (a part of the \
question was never researched, or the split fragmented one coherent part) — \
not from drafting or evidence problems. Default false.
"""


def pack_evidence(
    evidence: Sequence[EvidenceChunk], max_chars_per_chunk: int = MAX_CHARS_PER_CHUNK
) -> str:
    """Format evidence chunks for the model, one block per chunk, each headed
    by its citation id and source (ticker, form, section, filing date)."""
    blocks: list[str] = []
    for chunk in evidence:
        text = chunk.text
        if len(text) > max_chars_per_chunk:
            text = text[:max_chars_per_chunk] + " …[truncated]"
        filing_date = chunk.metadata.get("filing_date", "unknown date")
        header = (
            f"[{chunk.chunk_id}] {chunk.ticker} {chunk.form_type} ({filing_date}) — {chunk.section}"
        )
        blocks.append(f"{header}\n{text}")
    return "\n\n---\n\n".join(blocks)


def pack_evidence_by_sub_question(
    evidence: Sequence[EvidenceChunk],
    sub_questions: Sequence[SubQuestion],
    *,
    chunks_per_sub_question: int = DEFAULT_CHUNKS_PER_SUB_QUESTION,
    max_chars_per_chunk: int = MAX_CHARS_PER_CHUNK,
) -> str:
    """Fan-in packing: evidence grouped per sub-question with a per-group
    chunk budget, best-scored first, deduping identical chunks retrieved by
    multiple branches (a chunk appears once, under its first sub-question).
    Exhausted sub-questions get an explicit gap note instead of evidence."""
    seen: set[str] = set()
    by_sub: dict[str | None, list[EvidenceChunk]] = {}
    for chunk in sorted(
        evidence, key=lambda c: c.score if c.score is not None else -1.0, reverse=True
    ):
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        by_sub.setdefault(chunk.sub_question_id, []).append(chunk)

    known_ids = {sq.id for sq in sub_questions}
    blocks: list[str] = []
    for sq in sub_questions:
        header = f"## Sub-question {sq.id}: {sq.text}"
        if sq.status == "exhausted":
            blocks.append(
                f"{header}\nResearch for this sub-question failed — no evidence is "
                "available. Say so explicitly in the answer instead of guessing."
            )
            continue
        group = by_sub.get(sq.id, [])[:chunks_per_sub_question]
        body = pack_evidence(group, max_chars_per_chunk) if group else "(no evidence retrieved)"
        blocks.append(f"{header}\n{body}")
    # Chunks whose branch is no longer current (e.g. before a re-decomposition)
    # stay visible so previously cited ids remain resolvable.
    leftover = [c for sub_id, chunks in by_sub.items() if sub_id not in known_ids for c in chunks]
    if leftover:
        blocks.append(
            "## Other retrieved evidence\n"
            f"{pack_evidence(leftover[:chunks_per_sub_question], max_chars_per_chunk)}"
        )
    return "\n\n".join(blocks)


def build_synthesis_messages(
    question: str,
    evidence: Sequence[EvidenceChunk],
    *,
    sub_questions: Sequence[SubQuestion] = (),
    chunks_per_sub_question: int = DEFAULT_CHUNKS_PER_SUB_QUESTION,
    prior_draft: str | None = None,
    rejection_feedback: Sequence[str] = (),
    critic_feedback: str | None = None,
) -> list[BaseMessage]:
    """The full message list for the synthesis call (initial or revision).

    With multiple sub-questions the evidence is packed per sub-question
    (fan-in); a single (passthrough) sub-question keeps the flat packing —
    no ceremony for trivial questions."""
    if len(sub_questions) > 1:
        packed = pack_evidence_by_sub_question(
            evidence, sub_questions, chunks_per_sub_question=chunks_per_sub_question
        )
    else:
        packed = pack_evidence(evidence)
    parts = [
        f"Question: {question}",
        f"Evidence from SEC filings:\n\n{packed}",
    ]
    if prior_draft is not None:
        parts.append(f"Your previous draft:\n\n{prior_draft}")
    if rejection_feedback:
        feedback_lines = "\n".join(f"- {item}" for item in rejection_feedback)
        parts.append(
            "Verification rejected these claims:\n"
            f"{feedback_lines}\n"
            "Revise the draft: re-ground each rejected claim in the evidence "
            "with a correct citation, or remove it entirely."
        )
    if critic_feedback:
        parts.append(f"Reviewer feedback on the previous draft:\n{critic_feedback}")
    return [
        SystemMessage(content=SYNTHESIS_SYSTEM_PROMPT),
        HumanMessage(content="\n\n".join(parts)),
    ]


def build_decompose_messages(
    question: str,
    *,
    max_sub_questions: int,
    critic_feedback: str | None = None,
) -> list[BaseMessage]:
    """Messages for the decomposer (initial split or critic-driven re-split)."""
    parts = [
        f"Analyst question: {question}",
        f"Split into at most {max_sub_questions} sub-questions (fewer is better).",
    ]
    if critic_feedback:
        parts.append(
            "A previous split of this question produced an answer that failed "
            f"review for being mis-decomposed. Reviewer feedback:\n{critic_feedback}\n"
            "Produce a corrected split."
        )
    return [
        SystemMessage(content=DECOMPOSE_SYSTEM_PROMPT),
        HumanMessage(content="\n\n".join(parts)),
    ]


def build_entailment_messages(
    claim_text: str, chunks: Sequence[EvidenceChunk]
) -> list[BaseMessage]:
    """Messages for the qualitative entailment judge (one claim)."""
    return [
        SystemMessage(content=ENTAILMENT_SYSTEM_PROMPT),
        HumanMessage(
            content=f"Claim: {claim_text}\n\nCited source excerpts:\n\n{pack_evidence(chunks)}"
        ),
    ]


def build_attribution_messages(
    claim_text: str, chunks: Sequence[EvidenceChunk]
) -> list[BaseMessage]:
    """Messages for the forward-looking attribution judge (one claim)."""
    return [
        SystemMessage(content=ATTRIBUTION_SYSTEM_PROMPT),
        HumanMessage(
            content=f"Claim: {claim_text}\n\nCited source excerpts:\n\n{pack_evidence(chunks)}"
        ),
    ]


def build_critic_messages(
    question: str, draft_answer: str, claims: Sequence[Claim]
) -> list[BaseMessage]:
    """Messages for the critic's rubric check on the full draft."""
    claim_lines = (
        "\n".join(f"- [{c.status}] ({c.claim_type}) {c.text}" for c in claims) or "(no claims)"
    )
    return [
        SystemMessage(content=CRITIC_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Question: {question}\n\n"
                f"Draft answer:\n\n{draft_answer}\n\n"
                f"Claims with verification status:\n{claim_lines}"
            )
        ),
    ]
