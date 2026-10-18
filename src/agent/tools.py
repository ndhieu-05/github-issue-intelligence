"""Tầng tool cho agent: danh mục tool và hàm thực thi.
 
Vòng lặp agent sẽ:
  1. gửi to_anthropic_tools() (hoặc to_openai_tools()) cho LLM,
  2. nhận yêu cầu gọi tool (tên + tham số JSON),
  3. gọi execute_tool(name, arguments),
  4. gửi result.content về cho LLM, kèm cờ lỗi nếu result.is_error.
"""

import json 
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from src.retrieval.fetch_details import fetch_details
from src.retrieval.graph_expand import graph_expand
from src.retrieval.semantic_search import semantic_search

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 8000
REPO_PATTERN = re.compile(r"^[\w.-]+/[\w.-]+$")

@dataclass
class ToolSpec:
    """
    Một tool: Những gì LLM nhìn thấy + hàm thật.
    """

    name: str
    description: str
    parameters: dict # JSON Schema, type = "object"
    func: Callable[...,Any]

@dataclass
class ToolResult:
    """
    Kết quả của một lần gọi tool
    """

    name: str
    arguments: dict
    content: str # chuỗi đưa lại cho LLM
    is_error: bool
    data: Any = None # dữ liệu gốc: dùng để ghi trace và thu thập URL cho citation

# ======== Danh mục tool
TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name = "semantic_search",
        description= (
            "Tìm các issue cũ trong repository có nội dung gần nghĩa nhất với một đoạn "
            "văn bản. Dùng làm bước đầu tiên để tìm issue ứng viên; có thể gọi lại với "
            "cách diễn đạt khác nếu kết quả chưa phù hợp. Trả về danh sách gồm repo, "
            "number, title, url, snippet và distance. Dùng repo và number của kết quả "
            "để gọi graph_expand hoặc fetch_details"
        ),
        parameters = {
            "type": "object",
            "properties": {
                "query_text": {
                    "type": "string",
                    "description": (
                        "Mô tả vấn đề cần tìm, bằng tiếng Anh (nội dung issue trong "
                        "repository là tiếng Anh): tiêu đề cộng phần mô tả chính."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "Số issue tối đa trả về.",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["query_text"],
        },
        func = semantic_search,
    ),

    ToolSpec(
        name = "graph_expand",
        description= (
            "Với một issue đã biết (repo và number lấy từ kết quả semantic_search), trả về "
            "bằng chứng liên kết trong graph của repository: "
            "(1) closed_by: pull request hoặc commit đã đóng issue này, kèm commit_count và "
            "danh sách commit của PR; đây là bằng chứng chắc chắn về cách vấn đề được giải "
            "quyết. "
            "(2) co_closed_issues: các issue khác bị đóng bởi cùng pull request đó, thường là "
            "cùng một vấn đề. "
            "(3) referenced_by: pull request hoặc issue khác có nhắc tới issue này; chỉ là "
            "tín hiệu yếu, cho biết có liên quan chứ chưa chắc đã giải quyết. "
            "counts là tổng số thật khi các danh sách bị cắt bớt. Nếu closed_by rỗng, issue "
            "có thể đã được đóng thủ công hoặc chưa có thông tin trong graph. Kết quả chỉ có "
            "tiêu đề và liên kết, không có nội dung chi tiết; muốn đọc nội dung hãy gọi "
            "fetch_details."
        ),
        parameters = {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": (
                        "Repository dạng 'owner/name', sao chép nguyên từ trường repo trong "
                        "kết quả của tool khác (ví dụ 'openbao/openbao')."
                    ),
                },
                "number": {
                    "type": "integer",
                    "description": "Số hiệu của issue, lấy từ trường number trong kết quả semantic_search."
                },
                "max_items": {
                    "type": "integer",
                    "description": (
                        "Số phần tử tối đa của mỗi danh sách, từ 1 đến 20 (mặc định 10). "
                        "counts luôn là tổng số thật."
                    ),
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["repo", "number"],
        },
        func=graph_expand,
    ),

    ToolSpec(
        name = "fetch_details",
        description = (
            "Đọc nội dung chi tiết của một issue, pull request hoặc commit cụ thể. "
            "Với issue: trả về mô tả (body), trạng thái và một số comment (vài comment đầu và "
            "cuối; comment cuối thường chứa cách giải quyết), kèm comment_count và "
            "omitted_comments. "
            "Với pull_request: tiêu đề, danh sách commit và các issue mà PR đã đóng. "
            "Với commit: mã commit và các PR chứa nó. "
            "Nếu kết quả có source là 'graph_only' kèm note, nghĩa là chỉ có thông tin tối "
            "thiểu (tiêu đề, liên kết); không được suy đoán nội dung còn thiếu. "
            "Dùng semantic_search hoặc graph_expand khi cần đọc kỹ một issue ứng viên "
            "hoặc một PR cụ thể; không dùng để tìm kiếm." 
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["issue", "pull_request", "commit"],
                    "description": "Loại đối tượng cần đọc.",
                },
                "repo": {
                    "type": "string",
                    "description": (
                        "Repository dạng 'owner/name', sao chép nguyên từ kết quả của tool "
                        "khác (PR và commit có thể thuộc repository khác với repository của issue)."
                    ),
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Với issue và pull_request: số hiệu, ví dụ '45'. "
                        "Với commit: mã oid đầy đủ 40 ký tự."
                    ),
                },
            },
            "required": ["kind", "repo", "ref"],
        },
        func=fetch_details,
    ),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {
    spec.name: spec for spec in TOOL_SPECS
}

# ========== Chuyển sang định dạng của nhà cung cấp LLM
def to_anthoropic_tools() -> list[dict]:
    """
    Danh sách tool theo định dạng Claude Messages API.
    """
    return [
        {"name": s.name, "description": s.description, "input_schema": s.parameters}
        for s in TOOL_SPECS
    ]

def to_openai_tools() -> list[dict]:
    """
    Danh sách tool theo định dạng OpenAI-compatible (function calling).
    """
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters
            },
        }
        for s in TOOL_SPECS
    ]

# ======= Kiểm tra và ép kiểu tham số 
def _to_int(name: str, value: Any) -> int:
    """
    Ép về số nguyên: chấp nhận 5, 5.0, "5", "#5"; từ chối bool và chuỗi không phải số.
    """
    if isinstance(value, bool):
        raise ValueError(f"Tham số {name} phải là số nguyên, nhận được {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"#?\s*-?\d+", value.strip()):
        return int(value.strip().lstrip("#").strip())
    raise ValueError(f"Tham số {name} phải là số nguyên, nhận được {value!r}")


def _to_str(name: str, value: Any) -> str:
    """Ép về chuỗi: chấp nhận cả số (45 thành "45"); cắt khoảng trắng; từ chối chuỗi rỗng."""
    if isinstance(value, bool):
        raise ValueError(f"Tham số {name} phải là chuỗi, nhận được {value!r}")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError(f"Tham số {name} phải là chuỗi, nhận được {value!r}")
    value = value.strip()
    if not value:
        raise ValueError(f"Tham số {name} không được để trống")
    return value

def _coerce_value(name: str, value: Any, schema: dict) -> Any:
    """Ép một tham số về đúng kiểu khai báo trong schema và kiểm tra ràng buộc.
 
    Raises:
        ValueError: nêu rõ tên tham số, giá trị nhận được và giá trị hợp lệ.
    """
    expected = schema.get("type")

    if expected == "integer":
        value = _to_int(name, value)
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"Tham số {name}={value} nhỏ hơn mức tối thiểu {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"Tham số {name}={value} lớn hơn mức tối đa {schema['maximum']}")
 
    elif expected == "string":
        value = _to_str(name, value)
        if "enum" in schema:
            normalized = value.lower().replace("-", "_").replace(" ", "_")
            if normalized not in schema["enum"]:
                raise ValueError(
                    f"Tham số {name}={value!r} không hợp lệ; chọn một trong: "
                    f"{', '.join(schema['enum'])}"
                )
            value = normalized
        if name == "repo" and not REPO_PATTERN.match(value):
            raise ValueError(
                f"Tham số repo phải có dạng 'owner/name' (ví dụ 'openbao/openbao'), nhận được {value!r}"
            )
 
    return value

def _validate_and_coerce(spec: ToolSpec, arguments: dict)->dict:
    """Kiểm tra tham số do LLM đưa vào theo spec.parameters.
 
    - Giá trị null được coi như không truyền.
    - Tham số không khai báo bị bỏ qua (có ghi log cảnh báo).
    - Tham số bắt buộc thiếu, sai kiểu hoặc ngoài khoảng cho phép thì ném ValueError
      với thông báo rõ ràng để LLM đọc được và tự sửa ở lần gọi sau.
    """
    props = spec.parameters.get("properties", {})
    required = spec.parameters.get("required", [])

    arguments = {
        k: v for k, v in arguments.items() if v is not None
    }

    missing = [
        k for k in required if k not in arguments
    ]
    if missing:
        raise ValueError(f"Thiếu tham số bắt buộc: {','.join(missing)}")

    args = {}
    for key, value in arguments.items():
        if key not in props:
            logger.warning("Tool %s nhận tham số lạ, bỏ qua: %s", spec.name, key)
            continue
        args[key] = _coerce_value(key, value, props[key])

    return args


# ======= Thực thi
def _dump_result(data: Any)->str:
    """Chuyển kết quả thành chuỗi JSON gọn, cắt nếu quá dài."""
    text = json.dumps(data, ensure_ascii = False, separators = (",",":"), default = str)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"... [đã cắt, kết quả gốc dài {len(text)} ký tự]"

def execute_tool(
    name: str,
    arguments: dict|None,
    overrides: dict[str, dict] | None= None,
)-> ToolResult:
    """Chạy một tool theo yêu cầu của LLM. Không bao giờ ném lỗi ra ngoài.
 
    Args:
        name: tên tool do LLM chọn.
        arguments: tham số JSON do LLM đưa vào.
        overrides: tham số do HỆ THỐNG ép vào theo từng tool, LLM không nhìn thấy
            và không thay đổi được. Ví dụ khi đánh giá:
            {"semantic_search": {"exclude_number": 1475}}.
 
    Returns:
        ToolResult. Lỗi (tool lạ, tham số sai, DB hỏng) được trả về với
        is_error=True và nội dung lỗi để LLM đọc và thử lại, thay vì làm sập vòng lặp.
        "Không tìm thấy" không phải lỗi: is_error=False kèm thông báo.
    """
    if arguments is None:
        arguments = {}

    if not isinstance(arguments, dict):
        return ToolResult(
            name, {}, f"Tham số của tool phải là một object JSON, nhận được: {arguments!r}", True
        )

    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        valid = ", ".join(TOOLS_BY_NAME)
        return ToolResult(name, arguments,f"Tool không tồn tại: {name!r}. Các tool hợp lệ: {valid}.", True
        )

    try:
        args = _validate_and_coerce(spec, arguments)
        args.update((overrides or {}).get(name, {}))
        data = spec.func(**args)

    except Exception as exc:
        logger.warning("Tool %s lỗi với tham số %s: %s", name, arguments, exc)
        return ToolResult(name, arguments, f"Lỗi khi chạy {name}: {exc}", True)
 
    if data is None:
        content = "Không tìm thấy kết quả nào khớp với tham số đã cho."
    elif data == []:
        content = "Không có kết quả."
    else:
        content = _dump_result(data)
 
    return ToolResult(name, arguments, content, False, data)