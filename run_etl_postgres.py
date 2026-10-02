import json
from pathlib import Path

import yaml

from src.storage.postgres_client import (
    get_connection,
    create_schema,
    upsert_repo,
    upsert_issue,
    insert_comments,
)


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "issues.json"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_issues():
    with open(RAW_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    config = load_config()
    issues = load_issues()

    owner = config["github"]["owner"]
    name = config["github"]["name"]

    conn = get_connection()

    failed = []

    try:
        create_schema(conn)

        repo_id = upsert_repo(
            conn,
            owner,
            name,
        )

        print(f"Repository: {owner}/{name}")
        print(f"repo_id: {repo_id}")
        print(f"Total issues: {len(issues)}")

        for i, issue in enumerate(issues):
            try:
                issue_id = upsert_issue(conn, repo_id, issue)
                comments = issue.get("comments", {}).get("nodes", [])
                insert_comments(conn, issue_id, comments)
                if (i + 1) % 50 == 0:
                    print(f"Đã nạp {i + 1}/{len(issues)}")
            except Exception as e:
                issue_number = issue.get("number")
                print(f"Lỗi ở issue #{issue_number}: {e}")
                failed.append(issue_number)

        print("ETL Hoàn thành.")
        print(f"Thất bại: {len(failed)}")

        if failed:
            print(f"Các issue lỗi: {failed}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()