"""Entry point cho GitHub Issue Investigation Agent.

Trách nhiệm:
- Giữ API run_agent() để code bên ngoài tiếp tục gọi agent.
- Dựng state ban đầu, gọi LangGraph và chuyển state cuối thành AgentRun.

Prompt nằm ở core/prompts.py, node ở core/nodes.py, routing ở core/routing.py,
graph ở core/graph.py, gọi LLM ở core/llm_client.py, tool ở tools.py.
"""

from dataclasses import dataclass, field
from typing import Any

from src.agent.core.graph import build_graph
from src.agent.core.llm_client import make_client  # noqa: F401  (re-export cho code cũ)
from src.agent.core.prompts import SYSTEM_PROMPT  # noqa: F401  (re-export cho code cũ)
from src.agent.core.state import AgentState

PROMPT_BODY_LIMIT = 3000  # số ký tự tối đa của body issue đưa vào prompt


@dataclass
class AgentRun:
    """Kết quả của một lần chạy agent, kèm dữ liệu để ghi trace và đánh giá."""

    answer: str
    stopped_reason: str  # "final": model tự dừng; "budget": hết số bước, bị ép trả lời
    steps: list[dict[str, Any]] = field(default_factory=list)  # reasoning trace theo từng bước
    tool_results: list[Any] = field(default_factory=list)  # chỉ các lần gọi thật (không trùng lặp)
    usage: dict[str, int] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
    )
    llm_calls: int = 0
    tool_calls: int = 0


def build_user_prompt(title: str, body: str | None) -> str:
    """Ghép issue mới thành câu hỏi đầu tiên cho agent (giống cách ghép text khi embed)."""
    body = (body or "").strip()
    if len(body) > PROMPT_BODY_LIMIT:
        body = body[:PROMPT_BODY_LIMIT] + "\n[... truncated]"
    return f"New GitHub issue to investigate.\n\nTitle: {title}\n\nDescription:\n{body or '(empty)'}"


def run_agent(
    user_prompt: str,
    client: Any,
    model: str,
    *,
    max_steps: int = 6,
    temperature: float = 0.0,
    min_call_interval: float = 4.0,
    system_prompt: str | None = None,
    overrides: dict[str, dict] | None = None,
    evidence_gate: bool = True,
) -> AgentRun:
    """Chạy agent cho một issue mới.

    Vòng lặp: gọi LLM; nếu LLM yêu cầu tool thì chạy hết các tool đó và gửi kết quả lại,
    lặp tới khi LLM trả lời (không gọi tool nữa) hoặc hết `max_steps` lượt gọi LLM.
    Hết ngân sách thì ép LLM trả lời bằng bằng chứng đã có.

    Args:
        user_prompt: nội dung issue mới (xem build_user_prompt).
        client: client OpenAI-compatible (make_client), hoặc đối tượng giả khi kiểm thử.
        model: tên model.
        max_steps: số lượt gọi LLM tối đa trước khi ép trả lời.
        temperature: nên để 0 cho kết quả tái hiện được.
        system_prompt: lời nhắc hệ thống (mặc định SYSTEM_PROMPT).
        overrides: tham số do hệ thống ép vào tool (ví dụ {"semantic_search":
            {"exclude_number": 1475}} khi đánh giá); LLM không nhìn thấy.
    """
    initial_state: AgentState = {
        "user_prompt": user_prompt,
        "messages": [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "steps": [],
        "tool_results": [],
        "seen_calls": {},
        "seen_candidates": [],
        "executed_tool_names": [],
        "max_steps": max_steps,
        "step_count": 0,
        "llm_calls": 0,
        "tool_calls": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "gate_blocks": 0,
        "needs_continue": False,
        "answer": "",
        "stopped_reason": "",
    }

    config = {
        "configurable": {
            "client": client,
            "model": model,
            "temperature": temperature,
            "min_call_interval": min_call_interval,
            "overrides": overrides,
            "evidence_gate": evidence_gate,
        },
        # Mỗi bước LLM tối đa ~2-3 node; chừa dư để không chạm giới hạn mặc định (25).
        "recursion_limit": max_steps * 3 + 10,
    }

    final = build_graph().invoke(initial_state, config=config)

    return AgentRun(
        answer=final.get("answer", ""),
        stopped_reason=final.get("stopped_reason", "budget"),
        steps=final.get("steps", []),
        tool_results=final.get("tool_results", []),
        usage=final.get("usage", {"prompt_tokens": 0, "completion_tokens": 0}),
        llm_calls=final.get("llm_calls", 0),
        tool_calls=final.get("tool_calls", 0),
    )