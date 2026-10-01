import json 
import os
import time
from pathlib import Path
import yaml

from src.ingestion.github_client import run_query
from src.ingestion.queries import ISSUES_QUERY

def ingest_issues(owner: str, name: str, max_issues: int | None = None) -> list[dict]:
    """
    Lấy issue đã đóng của 1 repo, có pagination.

    Args:
        owner, name: chủ repo và tên repo.
        max_issues: giới hạn số issue lấy về. None = lấy hết.

    Returns:
        Danh sách các issue (mỗi issue là 1 dict, giữ nguyên cấu trúc từ GraphQL).
    """
    # Khởi tạo cursor và danh sách kết quả
    cursor = None
    all_issues = []

    # Vòng lặp
    while True:
        # Gọi GraphQL API
        data = run_query(
            query=ISSUES_QUERY,
            variables={
                "owner": owner,
                "name": name,
                "cursor": cursor,
            }
        )

        # Lấy thông tin issues
        issues = data["repository"]["issues"] # dict: {"pageInfo": {...}, "nodes": [...]}
        nodes = issues["nodes"]
        page_info = issues["pageInfo"]

        # Thêm issues vào danh sách kết quả
        all_issues.extend(nodes)

        # In ra tiến độ
        print(
            f"Đã lấy: {len(all_issues)} | "
            f"rateLimit remaining: {data['rateLimit']['remaining']}"
        )

        # Điều kiện dừng
        done_enough = max_issues is not None and len(all_issues) >= max_issues
        if not page_info["hasNextPage"] or done_enough:
            break

        # Cursor cho lần gọi tiếp theo
        cursor = page_info["endCursor"]

        # Nghỉ giữa các request để tránh gọi API quá dồn dập
        time.sleep(0.5)

    if max_issues is not None:
        all_issues = all_issues[:max_issues]

    return all_issues

def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def main():
    config = load_config()["github"]
    OUTPUT_FILE = "data/raw/issues.json"

    issues = ingest_issues(
        owner=config["owner"],
        name=config["name"],
        max_issues=config["max_issues"],
    )

    os.makedirs("data/raw", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=2)

    print(f"\nXong. Đã lưu {len(issues)} issue vào {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
