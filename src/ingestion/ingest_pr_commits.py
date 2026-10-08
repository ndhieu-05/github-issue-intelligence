"""
Ingest commit của các Pull Request đã đóng issue (closer là PullRequest).

Đọc data/raw/issues.json, gom các PR duy nhất từ ClosedEvent.closer, gọi GitHub
GraphQL lấy metadata + commits của từng PR, ghi ra data/raw/pr_commits.json.

Chạy: python -m src.ingestion.ingest_pr_commits
"""

import json
import time
from pathlib import Path

from src.ingestion.github_client import run_query
from src.ingestion.queries import PR_COMMITS_QUERY
from src.utils import parse_repo_from_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ISSUES_PATH = PROJECT_ROOT / "data" / "raw" / "issues.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "raw" / "pr_commits.json"


def collect_closer_prs(issues: list[dict]) -> list[tuple[str, int]]:
    """Gom các Pull Request duy nhất đã đóng ít nhất một issue.

    Duyệt timelineItems của từng issue, lấy ClosedEvent có closer là PullRequest
    (bỏ qua closer là None hoặc Commit).

    Args:
        issues: nội dung data/raw/issues.json.

    Returns:
        Danh sách (repo, number) đã khử trùng lặp và sắp xếp,
        ví dụ [("openbao/go-kms-wrapping", 64), ("openbao/openbao", 45)].
    """
    prs = set()

    for issue in issues:
        for event in issue.get("timelineItems", {}).get("nodes", []):
            if event.get("__typename") != "ClosedEvent":
                continue

            closer = event.get("closer")
            if not closer or closer.get("__typename") != "PullRequest":
                continue

            url = closer.get("url")
            number = closer.get("number")
            if not url or number is None:
                continue

            prs.add((parse_repo_from_url(url), number))

    return sorted(prs)


def fetch_pr_commits(owner: str, name: str, number: int) -> dict | None:
    """Lấy metadata và danh sách commit của một Pull Request.

    Returns:
        Dict của pullRequest (gồm commits với totalCount, pageInfo, nodes),
        hoặc None nếu PR không tồn tại.
    """
    variables = {"owner": owner, "name": name, "number": number}
    result = run_query(PR_COMMITS_QUERY, variables)
    return result["repository"]["pullRequest"]


def main():
    with open(ISSUES_PATH, "r", encoding="utf-8") as f:
        issues = json.load(f)

    prs = collect_closer_prs(issues)
    print(f"Tìm thấy {len(prs)} PR duy nhất.")

    results = []

    for index, (repo, number) in enumerate(prs, start=1):
        owner, name = repo.split("/", 1)

        try:
            pr = fetch_pr_commits(owner, name, number)

            if pr is None:
                print(f"PR không tồn tại: {repo}#{number}")
            else:
                results.append(
                    {
                        "repo": repo,
                        "number": number,
                        "title": pr.get("title"),
                        "url": pr.get("url"),
                        "state": pr.get("state"),
                        "mergedAt": pr.get("mergedAt"),
                        "commits": pr.get("commits"),
                    }
                )

        except Exception as e:
            print(f"Lỗi khi fetch {repo}#{number}: {e}")

        if index % 50 == 0 or index == len(prs):
            print(f"Đã xử lý {index}/{len(prs)} PR")

        time.sleep(0.3)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Đã ghi {len(results)} PR vào {OUTPUT_PATH}")


if __name__ == "__main__":
    main()