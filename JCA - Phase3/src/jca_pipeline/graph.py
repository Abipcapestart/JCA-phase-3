"""Builds and compiles the LangGraph StateGraph for the Phase 3 pipeline."""

import logging
import time

from langgraph.graph import END, StateGraph

from .nodes import (
    PipelineState,
    agent1_node,
    agent2_node,
    agent3_node,
    blocked_end_node,
    compute_categories_node,
    deterministic_checks_node,
    merge_node,
    not_ready_end_node,
    precheck_node,
    route_after_agent1,
    route_after_precheck,
)

logger = logging.getLogger("jca_pipeline.phases")


def _instrumented(phase_name: str, node_fn):
    """Wraps a node function to log its start/end and elapsed time — every
    phase transition becomes visible on the console (at INFO) without
    touching the node's own logic. Full before/after state is logged at
    DEBUG for deeper troubleshooting."""

    def wrapper(state: PipelineState) -> PipelineState:
        pico_set_id = state.get("pico_set_id", "?")
        logger.info("PHASE %-22s START  pico_set_id=%s", phase_name, pico_set_id)
        logger.debug("PHASE %s input state: %s", phase_name, state)
        start = time.perf_counter()
        result = node_fn(state)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("PHASE %-22s DONE   (%.1f ms)", phase_name, elapsed_ms)
        logger.debug("PHASE %s output state: %s", phase_name, result)
        return result

    return wrapper


def build_graph():
    graph = StateGraph(PipelineState)

    graph.add_node("precheck", _instrumented("precheck", precheck_node))
    graph.add_node("agent1", _instrumented("agent1", agent1_node))
    graph.add_node(
        "compute_categories", _instrumented("compute_categories", compute_categories_node)
    )
    graph.add_node("agent2", _instrumented("agent2", agent2_node))
    graph.add_node(
        "deterministic_checks", _instrumented("deterministic_checks", deterministic_checks_node)
    )
    graph.add_node("agent3", _instrumented("agent3", agent3_node))
    graph.add_node("merge", _instrumented("merge", merge_node))
    graph.add_node("not_ready_end", _instrumented("not_ready_end", not_ready_end_node))
    graph.add_node("blocked_end", _instrumented("blocked_end", blocked_end_node))

    graph.set_entry_point("precheck")

    graph.add_conditional_edges(
        "precheck",
        route_after_precheck,
        {"agent1": "agent1", "not_ready_end": "not_ready_end"},
    )
    graph.add_conditional_edges(
        "agent1",
        route_after_agent1,
        {"compute_categories": "compute_categories", "blocked_end": "blocked_end"},
    )
    graph.add_edge("compute_categories", "agent2")
    graph.add_edge("agent2", "deterministic_checks")
    graph.add_edge("deterministic_checks", "agent3")
    graph.add_edge("agent3", "merge")

    graph.add_edge("merge", END)
    graph.add_edge("not_ready_end", END)
    graph.add_edge("blocked_end", END)

    return graph.compile()
