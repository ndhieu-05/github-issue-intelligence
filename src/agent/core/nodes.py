"""Các node xử lý từng bước trong LangGraph agent.

Mỗi node nhận state hiện tại và trả về các trường cần cập nhật.
"""

import json
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from src.agent.core.llm_client import assistant_message_dict, call_llm
from src.agent.core.prompts import (
    BUDGET_REMINDERS,
    EVIDENCE_GATE_NOTE,
    FINAL_ANSWER_NUDGE,
    NO_NEW_CANDIDATES_NOTE,
)
from src.agent.core.state import AgentState
from src.agent.tools import ToolResult, execute_tool, to_openai_tools

logger = logging.getLogger(__name__)

REQUIRED_EVIDENCE_TOOLS = {"graph_expand", "fetch_details"}
MAX_GATE_BLOCKS = 1

def _add_usage(state: AgentState, response: Any) -> dict[str, int]:
    """Cộng token của response vào bản sao của usage trong state."""
    usage = dict(state.get("usage", {}))
    usage.setdefault("prompt_tokens", 0)
    usage.setdefault("completion_tokens", 0)

    response_usage = getattr(response, "usage", None)
    if response_usage:
        usage["prompt_tokens"] += getattr(response_usage, "prompt_tokens", 0) or 0
        usage["completion_tokens"] += getattr(response_usage, "completion_tokens", 0) or 0
    return usage


# ============================ call_llm ============================
def call_llm_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Gọi LLM một lượt rồi ghi phản hồi vào messages/steps/usage.

    Node này không tự chọn node kế tiếp (việc đó thuộc routing.py), nhưng đặt cờ
    `needs_continue` khi evidence gate chặn một câu trả lời cuối đưa ra quá sớm.
    """
    runtime = config.get("configurable", {})
    client = runtime["client"]
    model = runtime["model"]
    temperature = runtime.get("temperature", 0.0)
    min_interval = runtime.get("min_call_interval", 4.0)

    messages = list(state.get("messages", []))
    if not messages:
        raise ValueError("State chưa có messages (cần system + user prompt).")

    response, seconds = call_llm(
        client=client,
        model=model,
        messages=messages,
        tools=to_openai_tools(),
        temperature=temperature,
        min_interval=min_interval,
    )

    message = response.choices[0].message
    messages.append(assistant_message_dict(message))

    step_count = state.get("step_count", 0) + 1
    record: dict[str, Any] = {
        "step": step_count,
        "llm_seconds": round(seconds, 2),
        "assistant_text": message.content,
        "tool_calls": [],
    }
    steps = list(state.get("steps", []))
    steps.append(record)

    updates: dict[str, Any] = {
        "messages": messages,
        "steps": steps,
        "usage": _add_usage(state, response),
        "llm_calls": state.get("llm_calls", 0) + 1,
        "step_count": step_count,
        "needs_continue": False,
    }

    if message.tool_calls:
        return updates

    # LLM muốn trả lời cuối: nếu đã có ứng viên mà chưa kiểm tra đủ bằng chứng
    # (graph_expand + fetch_details) thì chưa chấp nhận, bắt điều tra tiếp.
    gate_blocks = state.get("gate_blocks", 0)
    evidence_gate = runtime.get("evidence_gate", True)

    seen_candidates = state.get("seen_candidates", [])
    missing_tools = (
        REQUIRED_EVIDENCE_TOOLS
        - set(state.get("executed_tool_names", []))
    )
    max_steps = state.get("max_steps", 6)

    if (
        evidence_gate
        and seen_candidates
        and missing_tools
        and step_count < max_steps
        and gate_blocks < MAX_GATE_BLOCKS
    ):
        missing = ", ".join(sorted(missing_tools))

        messages.append({
            "role": "user",
            "content": (
                EVIDENCE_GATE_NOTE
                + f"\nStill required: {missing}.\n"
                + "Use the existing candidate(s); avoid another "
                "near-duplicate semantic_search."
            ),
        })

        record["evidence_gate"] = {
            "blocked_early_final": True,
            "missing_tools": sorted(missing_tools),
            "gate_block": gate_blocks + 1,
        }

        updates["needs_continue"] = True
        updates["gate_blocks"] = gate_blocks + 1
        return updates

    updates["answer"] = message.content or ""
    updates["stopped_reason"] = "final"
    return updates

# ============================ execute_tools ============================
def _run_tool_call(
    call: dict[str, Any],
    overrides: dict[str, dict] | None,
    seen_calls: dict[str, ToolResult],
    seen_candidates: set,
) -> tuple[ToolResult, dict[str, Any]]:
    """Chạy một lời gọi tool của LLM. Không ném lỗi: lỗi trả về dưới dạng ToolResult.

    Bản ghi trace có "duplicate"=True nếu LLM gọi lại đúng tool với đúng tham số (không chạy
    lại, chỉ nhắc LLM), và "no_new_candidates"=True nếu semantic_search chỉ trả về các ứng
    viên đã thấy (nội dung được thay bằng lời nhắc ngắn để tiết kiệm token).
    `seen_calls` và `seen_candidates` được sửa trực tiếp.
    """
    function = call.get("function", {})
    name = function.get("name", "unknown_tool")
    raw_arguments = function.get("arguments") or "{}"
    record: dict[str, Any] = {"name": name, "duplicate": False}

    try:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Arguments phải là một JSON object.")
    except (json.JSONDecodeError, ValueError) as exc:
        result = ToolResult(name, {}, f"Tham số tool không hợp lệ: {exc}", True)
        record.update(arguments=raw_arguments, is_error=True, result_chars=len(result.content))
        return result, record

    key = name + json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)

    if key in seen_calls:
        record["duplicate"] = True
        result = ToolResult(
            name,
            arguments,
            "Bạn đã gọi tool này với đúng tham số này ở bước trước; hãy dùng kết quả đó "
            "hoặc đổi cách tìm.",
            True,
        )
    else:
        result = execute_tool(name, arguments, overrides)
        seen_calls[key] = result

        if name == "semantic_search" and isinstance(result.data, list) and result.data:
            found = {
                (item.get("repo"), item.get("number"))
                for item in result.data
                if item.get("repo") is not None and item.get("number") is not None
            }
            if found and found <= seen_candidates:
                numbers = ", ".join(f"#{n}" for _, n in sorted(found, key=str))
                result = ToolResult(
                    name,
                    arguments,
                    NO_NEW_CANDIDATES_NOTE.format(numbers=numbers),
                    result.is_error,
                    result.data,
                )
                record["no_new_candidates"] = True
            seen_candidates |= found

    record.update(arguments=arguments, is_error=result.is_error, result_chars=len(result.content))
    return result, record


def execute_tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Thực thi mọi tool call trong tin nhắn assistant gần nhất."""
    runtime = config.get("configurable", {})
    overrides = runtime.get("overrides")

    messages = list(state.get("messages", []))
    if not messages:
        raise ValueError("Không có messages để đọc tool calls.")

    tool_calls = messages[-1].get("tool_calls") or []
    if not tool_calls:
        return {}

    steps = list(state.get("steps", []))
    tool_results = list(state.get("tool_results", []))
    seen_calls = dict(state.get("seen_calls", {}))
    seen_candidates = set(state.get("seen_candidates", []))
    executed_names = set(state.get("executed_tool_names", []))
    tool_call_count = state.get("tool_calls", 0)

    # Ghi trace vào bước LLM vừa thực hiện (bản sao để không sửa state đầu vào).
    if steps:
        current_record = dict(steps[-1])
        current_record["tool_calls"] = list(current_record.get("tool_calls", []))
    else:
        current_record = {"step": state.get("step_count", 0), "tool_calls": []}

    for call in tool_calls:
        result, record = _run_tool_call(call, overrides, seen_calls, seen_candidates)

        tool_call_count += 1
        if not record["duplicate"]:
            tool_results.append(result)
            if not result.is_error:
                executed_names.add(record["name"])

        current_record["tool_calls"].append(record)

        # Mỗi tool_call phải có đúng một tin nhắn "tool" tương ứng.
        messages.append({
            "role": "tool",
            "tool_call_id": call.get("id", ""),
            "content": result.content,
        })

    # Nhắc ngân sách ngay trong tin nhắn tool cuối (giữ đúng thứ tự assistant, tool, assistant).
    remaining = state.get("max_steps", 6) - state.get("step_count", 0)
    reminder = BUDGET_REMINDERS.get(remaining)
    if reminder:
        last = dict(messages[-1])
        last["content"] += "\n\n" + reminder
        messages[-1] = last
        current_record["budget_reminder"] = remaining

    if steps:
        steps[-1] = current_record
    else:
        steps.append(current_record)

    return {
        "messages": messages,
        "steps": steps,
        "tool_results": tool_results,
        "seen_calls": seen_calls,
        "seen_candidates": list(seen_candidates),
        "executed_tool_names": sorted(executed_names),
        "tool_calls": tool_call_count,
    }


# ============================ force_final ============================
def force_final_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Hết ngân sách: ép LLM trả lời bằng bằng chứng đã có, không cho gọi thêm tool."""
    runtime = config.get("configurable", {})

    messages = list(state.get("messages", []))
    messages.append({"role": "user", "content": FINAL_ANSWER_NUDGE})

    response, seconds = call_llm(
        client=runtime["client"],
        model=runtime["model"],
        messages=messages,
        tools=to_openai_tools(),
        temperature=runtime.get("temperature", 0.0),
        tool_choice="none",
        min_interval=runtime.get("min_call_interval", 4.0),
    )

    message = response.choices[0].message
    messages.append(assistant_message_dict(message))

    steps = list(state.get("steps", []))
    steps.append({
        "step": state.get("step_count", 0) + 1,
        "llm_seconds": round(seconds, 2),
        "assistant_text": message.content,
        "tool_calls": [],
        "forced_final": True,
    })

    return {
        "messages": messages,
        "steps": steps,
        "usage": _add_usage(state, response),
        "llm_calls": state.get("llm_calls", 0) + 1,
        "answer": message.content or "",
        "stopped_reason": "budget",
    }


# ============================ finalize ============================
def finalize_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Chuẩn hóa kết quả khi graph kết thúc."""
    answer = (state.get("answer") or "").strip()
    if not answer:
        answer = "Agent chưa tạo được câu trả lời cuối cùng từ các bằng chứng đã thu thập."

    return {
        "answer": answer,
        "stopped_reason": state.get("stopped_reason") or "budget",
    }