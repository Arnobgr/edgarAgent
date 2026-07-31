"""Deterministic claim retraction: excise a claim's text from the draft.

Retraction is a code step, not an LLM call (plan §design): asking the model
to "rewrite without claim X" would reintroduce the compliance-trust problem
verification exists to remove. Removal is sentence-granular — the sentence
containing the claim (exact substring, or highest token overlap) is dropped.

Excision is a **set** operation, not a loop of independent edits (ADR 0021).
One draft's claims overlap heavily in token space — g-011's were all "Data
Center revenue was $X billion, up Y% year-on-year" — so re-deriving each
claim's target against a draft that mutates as it goes made claims delete
each other's sentences: one removed sentence carried 16 claims above the
threshold, and 26 of 204 answer sentences went that way across the
2026-07-28 run. The assignment is therefore computed once over the
un-mutated draft, at most one sentence per claim and one claim per sentence,
and only then applied.
"""

import logging
import re
from collections.abc import Sequence

logger = logging.getLogger(__name__)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[a-z0-9]+")
# Minimum share of the claim's tokens a sentence must contain to be excised.
MIN_OVERLAP = 0.5

# (paragraph index, sentence index) — a sentence's address in the draft.
_Position = tuple[int, int]


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) >= 3}


def _split_sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(paragraph) if s.strip()]


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _sentences(draft: str) -> list[tuple[_Position, str]]:
    return [
        ((pi, si), sentence)
        for pi, paragraph in enumerate(draft.split("\n\n"))
        for si, sentence in enumerate(_split_sentences(paragraph))
    ]


def _overlap(claim_core: str, claim_tokens: set[str], sentence: str) -> float:
    """Share of the claim's tokens this sentence carries; an exact substring
    outscores any token overlap."""
    if claim_core and claim_core in sentence:
        return 2.0
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & _tokens(sentence)) / len(claim_tokens)


def best_overlap(draft: str, claim_text: str) -> float:
    """The strongest claim-to-sentence match anywhere in the draft.

    Called either side of an excision pass to decide whether a claim's text
    is still in the prose. That comparison is the only honest way to tell a
    real retraction from a failed one: a claim whose sentence *another*
    claim's excision removed has genuinely left the answer and must count as
    retracted, while a claim whose text survives must not (ADR 0021)."""
    core = claim_text.strip().rstrip(".")
    tokens = _tokens(core)
    return max((_overlap(core, tokens, s) for _, s in _sentences(draft)), default=0.0)


def excise_spans(draft: str, spans: Sequence[str]) -> str:
    """Remove each claim's own quoted span from the draft.

    The synthesizer wrote the prose and the claim list together and knows
    which sentence states which claim; capturing that at synthesis makes
    removal exact (ADR 0023). Two claims quoting the same sentence remove it
    once — deletion is idempotent, so there is no order to get wrong and no
    sentence can be taken from another claim by accident.
    """
    body = draft
    for span in spans:
        stripped = span.strip()
        if stripped and stripped in body:
            body = body.replace(stripped, "")
    return _tidy(body) if body != draft else draft


def claim_present(draft: str, claim_text: str, answer_span: str) -> bool:
    """Whether a claim's text is still in the prose.

    A quoted span answers this exactly. Token overlap is the fallback for a
    claim that carries none — an older checkpoint, or a model that would not
    quote even after repair — and its use is counted so the fallback's
    reach stays visible rather than assumed to be zero."""
    stripped = answer_span.strip()
    if stripped:
        return stripped in draft
    logger.debug("claim has no answer span; falling back to token overlap")
    return best_overlap(draft, claim_text) >= MIN_OVERLAP


def excise_claims(draft: str, claim_texts: Sequence[str]) -> str:
    """Remove the sentences carrying `claim_texts`, assigned globally.

    Each claim takes at most one sentence and each sentence serves at most
    one claim. Ties break toward the strongest match, then the earliest
    claim, then the earliest sentence — the old `score >= best_score` let
    whichever sentence came last win a tie, so the result depended on
    iteration order.

    Demoted to a fallback by ADR 0023: claims that carry their own quoted
    span go through `excise_spans` and never reach this heuristic.
    """
    sentences = _sentences(draft)
    scored: list[tuple[float, int, int]] = []
    for ci, text in enumerate(claim_texts):
        core = text.strip().rstrip(".")
        if not core:
            continue
        tokens = _tokens(core)
        for si, (_, sentence) in enumerate(sentences):
            score = _overlap(core, tokens, sentence)
            if score >= MIN_OVERLAP:
                scored.append((-score, ci, si))
    scored.sort()

    claimed: set[int] = set()
    taken: set[_Position] = set()
    for _, ci, si in scored:
        position = sentences[si][0]
        if ci in claimed or position in taken:
            continue
        claimed.add(ci)
        taken.add(position)
    if not taken:
        return draft

    paragraphs = [
        " ".join(
            sentence
            for si, sentence in enumerate(_split_sentences(paragraph))
            if (pi, si) not in taken
        )
        for pi, paragraph in enumerate(draft.split("\n\n"))
    ]
    return _tidy("\n\n".join(p for p in paragraphs if p.strip()))
