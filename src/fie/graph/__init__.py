"""Orchestrator: the LangGraph graph.

Phase 1 is the cyclic core — research → synthesize → verify with the
verify→research loop-back, deterministic retraction, and the critic gate.
Phase 2 adds the decomposer and cross-node fan-out on the same skeleton.
"""

from fie.graph.build import GraphLimits, build_graph
from fie.graph.retract import excise_claim

__all__ = ["GraphLimits", "build_graph", "excise_claim"]
