"""Entry point cho GitHub Issue Investigation Agent.

Trách nhiệm:
- Giữ API run_agent() để code bên ngoài tiếp tục gọi agent.
- Dựng state ban đầu, gọi LangGraph và chuyển state cuối thành AgentRun.

Prompt nằm ở core/prompts.py, node ở core/nodes.py, routing ở core/routing.py,
graph ở core/graph.py, gọi LLM ở core/llm_client.py, tool ở tools.py.
"""
import json

from dataclasses import dataclass, field
from typing import Any

from src.agent.tools import execute_tool
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

def build_seed_query(
    title: str,
    body: str | None
)-> str:
    """
    Dạng title + body
    """
    body = (body or "").strip()[:PROMPT_BODY_LIMIT]
    return f"{title}\n\n{body}".strip()

def run_agent(
    user_prompt: str,
    client: Any,
    model: str,
    *,
    seed_query: str|None = None,
    seed_top_k: int = 8,
    max_steps: int = 15,
    temperature: float = 0.0,
    min_call_interval: float = 4.0,
    system_prompt: str | None = None,
    overrides: dict[str, dict] | None = None,
    evidence_gate: bool = True,
    blocked_issues: set[tuple[str, int]] | None = None,
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
        blocked_issues: tập (repo, number) mà graph_expand/fetch_details phải coi như
            không tồn tại (dùng khi đánh giá, để agent không tra chính issue đang test).
    """
    # Chạy seed_search trước
    seed_result = None
    user_content = user_prompt
    if seed_query:
        seed_args = {
            "query_text": seed_query,
            "top_k": seed_top_k
        }
        seed_result = execute_tool("semantic_search", seed_args, overrides, blocked_issues)
        if not seed_result.is_error and seed_result.data:
            user_content += (
                "\n\n[Initial search results, already run for you with this issue's text]\n"
                + seed_result.content
            )

    initial_state: AgentState = {
        "user_prompt": user_prompt,
        "messages": [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
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

    if seed_result and not seed_result.is_error and seed_result.data:
        key = "semantic_search"  + json.dumps(seed_args, sort_keys = True, ensure_ascii= False, default= str)
        initial_state["seen_calls"] = {key: seed_result}
        initial_state["seen_candidates"] = [
            (i["repo"], i["number"]) for i in seed_result.data if i.get("repo") and i.get("number")
        ]
        initial_state["tool_results"] = [seed_result]
        initial_state["executed_tool_names"] = ["semantic_search"]
        initial_state["tool_calls"] = 1
        initial_state["steps"] = [{
            "step": 0, "llm_seconds": 0, "assistant_text": None, "seed": True,
            "tool_calls": [{"name": "semantic_search", "duplicate": False, "seed": True,
                "arguments": seed_args, "is_error": False,
                "result_chars": len(seed_result.content)}],
        }]

    config = {
        "configurable": {
            "client": client,
            "model": model,
            "temperature": temperature,
            "min_call_interval": min_call_interval,
            "overrides": overrides,
            "evidence_gate": evidence_gate,
            "blocked_issues": blocked_issues
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