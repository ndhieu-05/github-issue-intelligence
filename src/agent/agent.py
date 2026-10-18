"""Vòng lặp agent: LLM tự gọi tool để thu thập bằng chứng rồi trả lời có trích dẫn.
 
Dùng API tương thích OpenAI (Gemini và Groq đều có endpoint này). Đổi nhà cung cấp
bằng mục `llm` trong config/config.yaml, không phải sửa code.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from src.agent.tools import ToolResult, execute_tool, to_openai_tools

logger = logging.getLogger(__name__)

PROMPT_BODY_LIMIT = 3000 # số ký tự tối đa của body issue đưa vào prompt

SYSTEM_PROMPT = """\

You help maintainers of the GitHub repository openbao/openbao investigate a NEW issue by \
finding evidence in the repository's history.
 
Tools:
- semantic_search: find past issues similar to a piece of text. Write queries in English.
- graph_expand: for a known issue, see which pull requests or commits closed it (strong \
evidence), which other issues the same PR closed, and what references it (weak evidence).
- fetch_details: read the full content of one issue, pull request or commit.
 
How to work:
1. Start with semantic_search. If the results look unrelated, search again with different wording.
2. For the most promising candidates, call graph_expand to see how they were resolved, then \
fetch_details on the few that matter (the issue discussion or the closing PR).
3. Stop as soon as you have enough evidence. Your number of steps is limited, so do not call \
tools you do not need.
 
Rules:
- Use only facts that appear in tool results. Never invent issues, pull requests, commits, \
causes or fixes.
- Distinguish evidence strength: closed_by is strong (it resolved the issue); referenced_by and \
semantic similarity are weak.
- If a result has source "graph_only", you only know its title and link; do not guess its content.
- If nothing relevant is found, say so clearly instead of guessing.
 
Final answer: (1) the most likely related past issues and why, (2) how they were resolved \
(pull requests or commits) if known, (3) what remains uncertain. After EVERY claim, cite the URL \
of the issue, pull request or commit it comes from, copied exactly from a tool result. Answer in \
the language of the new issue.
"""

FINAL_ANSWER_NUDGE = (
    "You have used all available tool calls. Write the final answer now, using only the "
    "evidence already collected, and say what remains uncertain." 
)


@dataclass
class AgentRun:
        """
        Kết quả của một lần chạy agent, kèm dữ liệu để ghi trace và đánh giá.
        """

        answer: str
        stopped_reason: str # "final": model tự dừng; "budget": hết số bước, bị ép trả lời
        steps: list[dict] = field(default_factory = list) # reasoning trace theo từng bước
        tool_results: list[ToolResult] = field(default_factory= list) # chỉ các lần gọi thật
        usage: dict = field(default_factory= lambda: 
            {
                "prompt_tokens": 0, 
                "completion_tokens":0
            }
        )
        llm_calls: int = 0
        tool_calls: int = 0

def make_client(llm_config: dict):
    """
    Tạo client OpenAI-compatible từ mục 'llm' của config

    Khóa API đọc từ biến môi trường LLM_API_KEY. 
    """
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise ValueError("Chưa đặt biến môi trường LLM_API_KEY")

    return OpenAI(
        api_key= api_key,
        base_url = llm_config["base_url"],
        max_retries = llm_config.get("max_retries", 5),
    )

def build_user_prompt(title: str, body: str | None) -> str:
    """
    Ghép issue mới thành câu hỏi đầu tiên cho agent
    """
    body = (body or "").strip()
    if len(body) > PROMPT_BODY_LIMIT:
        body = body[:PROMPT_BODY_LIMIT] + "\n[... truncated]"
    return f"New GitHub issue to investigate.\n\nTitle: {title}\n\nDescription:\n{body or '(empty)'}"

def _assistant_message_dict(message: Any) -> dict:
    """
    Dựng lại tin nhắn assistant để gửi lại ở lượt sau, chỉ giữ lại các trường chuẩn.

    Không dùng model_dump() nguyên bản vì một số nhà cung cấp từ chối các trường lạ.
    Riêng 'extra_content' thì giữ nếu có
    """
    data: dict[str, Any] = {
        "role": "assistant",
        "content": message.content or ""
    }

    if message.tool_calls:
        data["tool_calls"] = []
        for call in message.tool_calls:
            item = {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments
                },
            }
            extra = getattr(call, "extra_content", None)

            if extra:
                item["extra_content"] = extra

            data["tool_calls"].append(item)
    return data

def _call_llm(client, model, messages, tools, temperature, tool_choice = None):
    """
    Một lượt gọi LLM; trả về (response, số giây)
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "temperature": temperature,
    }
    if tool_choice:
        kwargs["tool_choice"] = tool_choice

    start = time.perf_counter()
    response = client.chat.completions.create(**kwargs)
    return response, time.perf_counter() - start

def _add_usage(run: AgentRun, response: Any)-> None:
    usage = getattr(response, "usage", None)
    if usage:
        run.usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
        run.usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0

def _run_tool_call(call: Any, overrides: dict | None, seen_calls: dict)-> tuple[ToolResult, dict]:
    """
    Chạy một lời gọi tool của LLM.
    Không ném lỗi: mỗi lỗi  trả về dưới dạng ToolResult

    Returns:
        (kết quả, bản ghi trace). Bản ghi có "duplicate" = True nếu LLM gọi lại đúng tool với đúng tham số đã gọi trước đó (không chạy lại, chỉ nhắc LLM)
    """
    name = call.function.name
    record = {
        "name": name,
        "duplicate": False
    }

    try: 
        arguments = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError as exc:
        result = ToolResult(name, {}, f"Tham số không phải JSON hợp lệ : {exc}", True)
        record.update(
            arguments = call.function.arguments,
            is_error = True,
            result_chars = len(result.content)
        )
        return result, record

    key  = name + json.dumps(
        arguments, 
        sort_keys = True,
        ensure_ascii = False,
        default = str
    )
    if key in seen_calls:
        record["duplicate"] = True
        result = ToolResult(
            name,
            arguments,
            "Bạn đã gọi tool này với đúng tham số này ở bước trước: hãy dùng kết quả đó "
            "hoặc đổi cách tìm.",
            True,
        )

    else:
        result = execute_tool(name, arguments, overrides)
        seen_calls[key] = result

    record.update(
        arguments= arguments,
        is_error = result.is_error,
        result_chars = len(result.content)
    )
    return result, record

def run_agent(
    user_prompt: str,
    client: Any,
    model: str,
    *,
    max_steps: int = 6,
    temperature: float = 0.0,
    system_prompt: str = SYSTEM_PROMPT,
    overrides: dict[str, dict] | None = None,
)-> AgentRun:
    """Chạy agent cho một issue mới.
 
    Vòng lặp: gọi LLM, nếu LLM yêu cầu tool thì chạy hết các tool đó và gửi kết quả
    lại, lặp tới khi LLM trả lời (không gọi tool nữa) hoặc hết `max_steps` lượt gọi LLM.
    Hết ngân sách thì ép LLM trả lời bằng bằng chứng đã có.
 
    Args:
        user_prompt: nội dung issue mới (xem build_user_prompt).
        client: client OpenAI-compatible (make_client), hoặc đối tượng giả khi kiểm thử.
        model: tên model.
        max_steps: số lượt gọi LLM tối đa trước khi ép trả lời.
        temperature: nên để 0 cho kết quả tái hiện được.
        system_prompt: lời nhắc hệ thống.
        overrides: tham số do hệ thống ép vào tool (ví dụ {"semantic_search":
            {"exclude_number": 1475}} khi đánh giá); LLM không nhìn thấy.
 
    Returns:
        AgentRun với câu trả lời, lý do dừng, trace, token đã dùng.
    """
    tools = to_openai_tools()
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    run = AgentRun(
        answer = "",
        stopped_reason= "budget"
    )
    seen_calls: dict[str, ToolResult] = {}

    for step in range(1, max_steps + 1):
        response, seconds = _call_llm(client, model, messages, tools, temperature)
        run.llm_calls += 1
        _add_usage(run, response)

        message = response.choices[0].message
        messages.append(_assistant_message_dict(message))

        record = {
            "step": step,
            "llm_seconds": round(seconds, 2),
            "assistant_text": message.content,
            "tool_calls": [],
        }
        run.steps.append(record)

        if not message.tool_calls:
            run.answer = message.content or ""
            run.stopped_reason = "final"
            return run

        # Một lượt có thể có nhiều lời gọi tool song song: mỗi lời gọi cần đúng 1 tin nhắn "tool"
        for call in message.tool_calls:
            result, tool_record = _run_tool_call(call, overrides, seen_calls)
            run.tool_calls += 1
            if not tool_record["duplicate"]:
                run.tool_results.append(result)
            record["tool_calls"].append(tool_record)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result.content
                }
            )

    # Hết ngân sách: ép trả lời, ko gọi thêm
    messages.append({"role": "user", "content": FINAL_ANSWER_NUDGE})
    response, seconds = _call_llm(client, model, messages, tools, temperature, tool_choice = "none")
    run.llm_calls += 1
    _add_usage(run, response)
    message = response.choices[0].message
    run.answer = message.content or ""
    run.steps.append(
        {
            "step": max_steps + 1,
            "llm_seconds": round(seconds, 2),
            "assistant_text": message.content,
            "tool_calls": [],
            "forced_final": True
        }
    )
    return run