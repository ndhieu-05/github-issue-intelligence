r"""Lắp ráp LangGraph cho agent.

    START -> call_llm --(tool calls)--> execute_tools --(còn bước)--> call_llm
                 |  \--(gate chặn)--> call_llm        \--(hết bước)--> force_final
                 \--(trả lời cuối)--> finalize <------------------------/
"""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from src.agent.core.nodes import (
    call_llm_node,
    execute_tools_node,
    finalize_node,
    force_final_node,
)
from src.agent.core.routing import route_after_llm, route_after_tools
from src.agent.core.state import AgentState


@lru_cache(maxsize=1)
def build_graph():
    """Dựng và compile graph (cache vì graph không phụ thuộc runtime config)."""
    builder = StateGraph(AgentState)

    builder.add_node("call_llm", call_llm_node)
    builder.add_node("execute_tools", execute_tools_node)
    builder.add_node("force_final", force_final_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "call_llm")
    builder.add_conditional_edges(
        "call_llm",
        route_after_llm,
        {"call_llm": "call_llm", "execute_tools": "execute_tools", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "execute_tools",
        route_after_tools,
        {"call_llm": "call_llm", "force_final": "force_final"},
    )
    builder.add_edge("force_final", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()