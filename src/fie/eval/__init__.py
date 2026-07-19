"""Evaluation (Phase 3, workstream C).

Two layers, attributing failures to retrieval vs reasoning (blueprint
§5/§6): the retrieval eval (recall@k / MRR / nDCG over labeled queries —
also the harness that decides the embedding and reranker ADRs) and the
end-to-end gold set (grounding rate, hallucination rate, citation
accuracy, numeric fidelity, verifier P/R). Code lives here so the
`eval-retrieval` / `eval-agent` entry points are importable; the versioned
datasets, baselines and reports live in the repo-root `eval/` directory.
"""
