"""Deterministic claim retraction: excise a claim's text from the draft.

Retraction is a code step, not an LLM call (plan §design): asking the model
to "rewrite without claim X" would reintroduce the compliance-trust problem
verification exists to remove. Removal is sentence-granular — the sentence
containing the claim (exact substring, or highest token overlap) is dropped.
"""

import re

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[a-z0-9]+")
# Minimum share of the claim's tokens a sentence must contain to be excised.
MIN_OVERLAP = 0.5


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) >= 3}


def _split_sentences(paragraph: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(paragraph) if s.strip()]


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def excise_claim(draft: str, claim_text: str) -> tuple[str, bool]:
    """Remove the sentence carrying `claim_text` from `draft`.

    Returns the edited draft and whether anything was removed. If no sentence
    contains the claim text or overlaps it enough, the draft is returned
    unchanged — the claim is still listed as retracted by the caller, so it is
    never silently dropped.
    """
    claim_core = claim_text.strip().rstrip(".")
    claim_tokens = _tokens(claim_core)
    if not claim_core:
        return draft, False

    best: tuple[int, int] | None = None
    best_score = MIN_OVERLAP
    paragraphs = draft.split("\n\n")
    for pi, paragraph in enumerate(paragraphs):
        for si, sentence in enumerate(_split_sentences(paragraph)):
            if claim_core in sentence:
                best, best_score = (pi, si), 2.0
                break
            if claim_tokens:
                score = len(claim_tokens & _tokens(sentence)) / len(claim_tokens)
                if score >= best_score:
                    best, best_score = (pi, si), score
        if best_score >= 2.0:
            break

    if best is None:
        return draft, False
    pi, si = best
    sentences = _split_sentences(paragraphs[pi])
    del sentences[si]
    paragraphs[pi] = " ".join(sentences)
    return _tidy("\n\n".join(p for p in paragraphs if p.strip())), True
