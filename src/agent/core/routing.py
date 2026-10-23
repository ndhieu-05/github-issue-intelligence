"""Các hàm routing: quyết định node kế tiếp từ state hiện tại."""

from typing import Literal

from src.agent.core.state import AgentState


def route_after_llm(state: AgentState) -> Literal["call_llm", "execute_tools", "finalize"]:
    """Sau call_llm:
    - evidence gate chặn câu trả lời sớm -> gọi LLM tiếp,
    - LLM yêu cầu tool -> chạy tool,
    - còn lại (câu trả lời cuối) -> finalize.
    """
    if state.get("needs_continue"):
        return "call_llm"

    messages = state.get("messages", [])
    if messages and messages[-1].get("tool_calls"):
        return "execute_tools"

    return "finalize"


def route_after_tools(state: AgentState) -> Literal["call_llm", "force_final"]:
    """Sau execute_tools: còn ngân sách thì gọi LLM tiếp, hết thì ép trả lời."""
    if state.get("step_count", 0) >= state.get("max_steps", 6):
        return "force_final"
    return "call_llm"