"""Trạng thái dùng chung giữa các node trong LangGraph."""
 
from typing import Any, TypedDict
 
 
class AgentState(TypedDict, total=False):
    """Mỗi node đọc state hiện tại và chỉ trả về các trường cần cập nhật.
 
    total=False vì node trả về cập nhật từng phần; code đọc state dùng .get().
    """
 
    # Đầu vào
    user_prompt: str
 
    # Lịch sử hội thoại gửi cho LLM
    messages: list[dict[str, Any]]
 
    # Theo dõi quá trình
    steps: list[dict[str, Any]]          # reasoning trace theo từng bước
    tool_results: list[Any]              # chỉ các lần gọi thật (không trùng lặp)
    seen_calls: dict[str, Any]           # key (tên + tham số) -> ToolResult, để chặn gọi trùng
    seen_candidates: list[tuple]         # (repo, number) các issue đã thấy
    executed_tool_names: list[str]       # tool đã chạy thành công
 
    # Ngân sách và bộ đếm
    max_steps: int
    step_count: int
    llm_calls: int
    tool_calls: int
    usage: dict[str, int]
    gate_blocks: int
 
    # Điều khiển luồng
    needs_continue: bool                 # evidence gate chặn câu trả lời sớm -> gọi LLM tiếp
 
    # Kết quả
    answer: str
    stopped_reason: str                  # "final" | "budget"