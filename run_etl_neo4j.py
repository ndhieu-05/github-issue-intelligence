import json
from src.storage.config import load_config

from src.storage.neo4j_client import (
    get_driver, close_driver, 
    merge_issue, merge_closed_by, merge_referenced_by, merge_pr_commits
)

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "issues.json"
PR_COMMITS_PATH = PROJECT_ROOT / "data" / "raw" / "pr_commits.json"

def load_issues():
    with open(RAW_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_pr_commits():
    with open(PR_COMMITS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def determine_type(obj: dict)->str:
    """obj là closer hoặc source.

    Returns:
        "PullRequest" | "Commit" | "Issue"
    """
    return obj["__typename"]

def main():
    # Đọc config lấy owner/name
    config = load_config()
    # Đọc dữ liệu
    issues = load_issues()
    pr_commits = load_pr_commits()

    owner = config["github"]["owner"]
    name = config["github"]["name"]
    repo = f"{owner}/{name}"

    # Mở driver
    driver = get_driver()

    # Vòng lặp Merge Issue
    try:
        print(f"Repository: {repo}")
        print(f"Total isssues: {len(issues)}")

        for i, issue in enumerate(issues):
            try:
                merge_issue(driver, repo, issue)

                for event in issue["timelineItems"]["nodes"]:
                    if event["__typename"] == "ClosedEvent":
                        closer = event.get("closer")
                        merge_closed_by(
                            driver,
                            repo, 
                            issue["number"],
                            closer,
                            determine_type(closer) if closer else None,
                        )

                    elif event["__typename"] == "CrossReferencedEvent":
                        source = event.get("source")
                        merge_referenced_by(
                            driver,
                            repo,
                            issue["number"],
                            source,
                            determine_type(source) if source else None,
                        )

            except Exception as e:
                print(f"Lỗi ở Issue #{issue.get('number')}: {e}")

            if (i + 1) % 50 == 0:
                print(f"Đã nạp {i + 1}/{len(issues)} issue vào Neo4j")

        # Vòng lặp Merge PR -> Commit
        print(f"Total PR có commit data: {len(pr_commits)}")

        for i, pr in enumerate(pr_commits):
            try:
                pr_repo = pr["repo"]
                pr_number = pr["number"]

                commits = pr.get("commits", {}).get("nodes", [])

                for commit_node in commits:
                    commit = commit_node.get("commit")

                    if not commit:
                        continue

                    merge_pr_commits(
                        driver,
                        pr_repo,
                        pr_number,
                        commit,
                    )

            except Exception as e:
                print(
                    f"Lỗi ở PR "
                    f"{pr.get('repo')}#{pr.get('number')}: {e}"
                )

            if (i + 1) % 50 == 0:
                print(
                    f"Đã nạp commit của "
                    f"{i + 1}/{len(pr_commits)} PR"
                )

        print("ETL Neo4j hoàn thành.")

    finally:
        close_driver(driver)

if __name__ == "__main__":
    main()