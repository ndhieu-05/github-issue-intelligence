"""Tạo client và gọi LLM (API tương thích OpenAI)."""

import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

# Thời điểm bắt đầu request gần nhất (dùng để giãn cách request).
_last_call_started = 0.0


def make_client(llm_config: dict[str, Any] | None = None) -> OpenAI:
    """Tạo client OpenAI-compatible từ mục `llm` của config.

    Khóa API đọc từ biến môi trường (mặc định LLM_API_KEY, nạp từ .env).
    Việc thử lại khi gặp 429/5xx do SDK tự xử lý theo `max_retries`.
    """
    if llm_config is None:
        from src.storage.config import load_config  # import muộn: chỉ cần khi không truyền config

        llm_config = load_config()["llm"]

    load_dotenv()

    api_key_env = llm_config.get("api_key_env", "LLM_API_KEY")
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise ValueError(f"Chưa đặt biến môi trường {api_key_env} (trong file .env)")

    return OpenAI(
        api_key=api_key,
        base_url=llm_config.get("base_url"),
        max_retries=llm_config.get("max_retries", 5),
    )


def assistant_message_dict(message: Any) -> dict[str, Any]:
    """Dựng lại tin nhắn assistant để gửi lại ở lượt sau, chỉ giữ các trường chuẩn.

    Không dùng model_dump() nguyên bản vì một số nhà cung cấp từ chối các trường lạ
    (annotations, audio...). Riêng `extra_content` (Gemini có thể cần gửi lại) thì giữ nếu có.
    """
    data: dict[str, Any] = {"role": "assistant", "content": message.content or ""}

    if message.tool_calls:
        data["tool_calls"] = []
        for call in message.tool_calls:
            item: dict[str, Any] = {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            extra = getattr(call, "extra_content", None)
            if extra:
                item["extra_content"] = extra
            data["tool_calls"].append(item)

    return data


def _wait_for_rate_limit(min_interval: float = 0.0) -> None:
    """Giãn cách thời điểm bắt đầu các lần gọi LLM."""
    global _last_call_started

    if min_interval > 0:
        wait = min_interval - (time.monotonic() - _last_call_started)
        if wait > 0:
            time.sleep(wait)

    _last_call_started = time.monotonic()


def call_llm(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.0,
    tool_choice: str | dict[str, Any] | None = None,
    min_interval: float = 0.0,
) -> tuple[Any, float]:
    """Một lượt gọi LLM; trả về (response, số giây gọi)."""
    _wait_for_rate_limit(min_interval)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice

    start = time.perf_counter()
    response = client.chat.completions.create(**kwargs)
    elapsed = time.perf_counter() - start

    logger.debug("LLM request hoàn tất trong %.2f giây", elapsed)
    return response, elapsed