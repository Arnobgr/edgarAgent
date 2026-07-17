"""Orchestrator: the LangGraph graph.

Phase 0 is the minimal spine — research → synthesize. Later phases add the
decomposer, verifier, critic, and the verify→research loop-back as new nodes
and edges inside this module; the skeleton does not get restructured.
"""

from fie.graph.build import build_graph

__all__ = ["build_graph"]
