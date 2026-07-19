"""Orchestrator: the LangGraph graph.

Phase 1 built the cyclic core — research → synthesize → verify with the
verify→research loop-back, deterministic retraction, and the critic gate.
Phase 2 completes the topology: the decomposer, Send-based parallel
research fan-out, fan-in synthesis, targeted loop-backs, and the bounded
critic→decompose edge.
"""

from fie.graph.build import GraphLimits, build_graph
from fie.graph.retract import excise_claim

__all__ = ["GraphLimits", "build_graph", "excise_claim"]
