"""Build the Phase 0 graph: research → synthesize."""

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from fie.context import build_synthesis_messages
from fie.state import EngineState
from fie.tools.retrieval import Retriever


def build_graph(
    retriever: Retriever, synthesis_model: BaseChatModel, k: int = 6
) -> CompiledStateGraph[EngineState]:
    """Compile the graph over an injected retriever and synthesis model.

    Dependencies are injected (not constructed here) so tests can run the
    graph with fakes and later phases can swap retrieval implementations.
    """

    def research(state: EngineState) -> dict[str, object]:
        """Retrieve evidence for the question and pack the top-k into state."""
        return {"evidence": retriever.retrieve(state.question, k=k)}

    def synthesize(state: EngineState) -> dict[str, object]:
        """Draft an answer from the evidence, citing chunk ids inline."""
        messages = build_synthesis_messages(state.question, state.evidence)
        response = synthesis_model.invoke(messages)
        return {"draft_answer": response.text}

    graph: StateGraph[EngineState] = StateGraph(EngineState)
    graph.add_node("research", research)
    graph.add_node("synthesize", synthesize)
    graph.add_edge(START, "research")
    graph.add_edge("research", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()
