# ADR 0003 — The retrieval interface

**Date:** 2026-07-17 · **Phase:** 0 · **Status:** accepted

## Decision

Retrieval is defined as a small structural protocol in `fie.tools.retrieval`:

```python
@runtime_checkable
class Retriever(Protocol):
    def retrieve(self, query: str, k: int = 8) -> list[EvidenceChunk]: ...
```

Results come back best-first with `score` populated, each `EvidenceChunk`
carrying its citation trail (`accession_no`, `filing_date`, section) in
metadata. The graph receives a `Retriever` by injection and never constructs
one.

Phase 0's implementation is in-memory BM25 (`rank-bm25`) over section-aware
chunks — a deliberate placeholder.

## Rationale

- **The interface is the contract, the implementation is a phase detail.**
  Phase 3 replaces BM25 with Qdrant hybrid retrieval (dense + BM25 +
  reranking) behind the *same* method signature; the graph and tests don't
  change.
- A `Protocol` (structural typing) rather than an ABC keeps implementations
  dependency-free — the Phase 3 retriever won't need to import a base class,
  and test stubs satisfy the interface for free.
- Returning `EvidenceChunk` (not bare strings) everywhere keeps the citation
  trail attached from retrieval through synthesis to the final answer —
  non-negotiable for the verification phases.

## Consequences

- Retrieval quality in Phase 0 is naive by design; it only has to surface
  obviously relevant sections of one 10-K. Retrieval evaluation (recall@k,
  nDCG) arrives with the Phase 3 eval set.
