"""
Module ETL nạp dữ liệu Issue/PR/Commit vào Neo4j.

Dựng node Issue/PullRequest/Commit và relationship CLOSED_BY/REFERENCES
từ dữ liệu GitHub đã ingest (data/raw/issues.json), dùng MERGE để idempotent

"""

from src.storage.config import load_config
from src.utils import parse_repo_from_url

from neo4j import Driver, GraphDatabase
from typing import Optional

# Neo4j Connection
def get_driver()->Driver:
    """Mở kết nối driver tới Neo4j, đọc URI/user/password từ configs/config.yaml.

    Returns:
        Driver: đối tượng driver dùng để mở session và chạy Cypher.
    """
    config = load_config()
    neo4j_config = config["neo4j"]

    uri = neo4j_config["uri"]
    user = neo4j_config["user"]
    password = neo4j_config["password"]

    return GraphDatabase.driver(
        uri,
        auth = (user, password)
    )

def close_driver(driver: Driver)->None:
    """Đóng driver, giải phóng connection pool."""
    return driver.close()

def merge_issue(driver: Driver, repo:str, issue_dict:dict)->None:
    """Tạo/cập nhật 1 node :Issue.

    Args:
        driver: Neo4j driver 
        repo: "owner/name" của repo chứa issue này 
        issue_dict: 1 phần tử trong data/raw/issues.json, cần có number/title/url/
            state/createdAt, closedAt tùy chọn.
    """
    query = """
        MERGE (i:Issue {
            repo: $repo,
            number: $number
        })
        SET
            i.title = $title,
            i.url = $url,
            i.state = $state,
            i.createdAt = $createdAt,
            i.closedAt = $closedAt
    """

    with driver.session() as session:
        session.run(
            query,
            repo = repo,
            number = issue_dict["number"],
            title=issue_dict["title"],
            url=issue_dict["url"],
            state=issue_dict["state"],
            createdAt=issue_dict["createdAt"],
            closedAt=issue_dict.get("closedAt"),
        )

def merge_closed_by(
    driver: Driver,
    repo: str,
    issue_number: int,
    closer_dict: Optional[dict],
    closer_type: str,
) -> None:
    """Tạo cạnh (:Issue)-[:CLOSED_BY]->(:PullRequest | :Commit).

    Args:
        driver: Neo4j driver.
        repo: "owner/name" của repo chứa issue đang xét (dùng để MATCH issue,
            KHÔNG dùng cho node closer vì closer có thể thuộc repo khác).
        issue_number: số issue đang xét.
        closer_dict: giá trị closer từ ClosedEvent — None nếu đóng thủ công,
            có key "number" nếu là PullRequest, có key "oid" nếu là Commit.
        closer_type: "PullRequest" | "Commit", xác định từ closer_dict
            trước khi gọi hàm (dựa vào key nào có mặt).

    Raises:
        ValueError: nếu closer_type không phải 2 giá trị hợp lệ trên.
    """
    if closer_dict is None:
        return 

    closer_repo = parse_repo_from_url(closer_dict["url"])

    # Trường hợp 1 - Pull Request
    # {
    #     "number": 45,
    #     "title": "Fix something",
    #     "url": "..."
    # }
    # Có key "number" => Pull Request
    #
    # Graph: Issue #1 ──CLOSED_BY──> PR #45

    if closer_type == "PullRequest":
        query = """
            MATCH (i:Issue {
                repo: $repo,
                number: $issue_number
            })
            
            MERGE (p:PullRequest {
                repo: $closer_repo,
                number: $closer_number
            })

            SET 
                p.title = $title,
                p.url = $url

            MERGE (i)-[:CLOSED_BY]->(p)
        """

        params = {
            "repo": repo, # repo của Issue dùng để MATCH
            "closer_repo": closer_repo, # repo thật của PR 
            "issue_number": issue_number, 
            "closer_number": closer_dict["number"],
            "title": closer_dict.get("title"),
            "url": closer_dict.get("url"),
        }

    # Trường hợp 2 - Commit
    # {
    #     "oid": "abc123...",
    #     "url": "..."
    # }
    # Có key "oid" => Commit
    #
    # Graph: Issue #1 ──CLOSED_BY──> Commit abc123

    elif closer_type == "Commit":
        query = """
            MATCH (i:Issue {
                repo: $repo,
                number: $issue_number
            })

            MERGE (c:Commit{
                repo: $closer_repo,
                oid: $oid
            })

            SET 
                c.url = $url

            MERGE (i)-[:CLOSED_BY]->(c)
        """

        params = {
            "repo": repo,
            "closer_repo": closer_repo,
            "issue_number": issue_number,
            "oid": closer_dict["oid"],
            "url": closer_dict.get("url"),
        }

    # Trường hợp 3 - None
    # closer = None
    # 
    # → GitHub không cho biết cái gì đóng issue.
    # Không tạo node gì cả.

    else: 
        raise ValueError(f"Unknown closer_type: {closer_type}")

    with driver.session() as session:
        session.run(query, **params)

def merge_pr_commits(
    driver: Driver,
    repo: str,
    pr_number:int,
    commit_dict: Optional[dict],
)-> None:
    """Tạo/cập nhật node Commit và cạnh PR-[:INCLUDES]->Commit.

    Args:
        driver: Neo4j driver.
        repo: "owner/name" của repo chứa Pull Request.
        pr_number: số Pull Request.
        commit_dict: thông tin commit lấy từ pr_commits.json,
            cần có oid, url; messageHeadline tùy chọn.
    """

    query = """
        MATCH (p:PullRequest{
            repo: $repo,
            number: $pr_number
        })

        MERGE (c:Commit{
            repo: $repo,
            oid: $oid
        })

        SET
            c.url = $url,
            c.messageHeadline = $message_headline

        MERGE (p)-[:INCLUDES]->(c)
    """

    params = {
        "repo": repo,
        "pr_number": pr_number,
        "oid": commit_dict["oid"],
        "url": commit_dict.get("url"),
        "message_headline": commit_dict.get("messageHeadline"),
    }

    with driver.session() as session:
        session.run(query, **params)

def merge_referenced_by(
    driver: Driver,
    repo: str,
    issue_number: int,
    source_dict: Optional[dict],
    source_type: str,
) -> None:
    """Tạo cạnh (:PullRequest | :Issue)-[:REFERENCES]->(:Issue hiện tại).

    Args:
        driver: Neo4j driver.
        repo: "owner/name" của repo chứa issue đang xét.
        issue_number: số issue đang xét.
        source_dict: giá trị source từ CrossReferencedEvent — có key "number"
            dù là PullRequest hay Issue (phân biệt qua source_type, không qua key).
        source_type: "PullRequest" | "Issue".

    Raises:
        ValueError: nếu source_type không phải 2 giá trị hợp lệ trên.
    """
 
    if source_dict is None: 
        return
    
    source_repo = parse_repo_from_url(source_dict["url"])

    # Issue #1
    # timeline:
    #     CrossReferencedEvent
    #         source = PR #2
    #
    # Graph: PR #2 ──REFERENCES──> Issue #1

    if source_type == "PullRequest":
        query = """
            MATCH (i:Issue{
                repo: $repo,
                number: $issue_number
            })

            MERGE (p:PullRequest{
                repo: $source_repo,
                number: $source_number
            })

            SET
                p.title = $title,
                p.url = $url
        
            MERGE (p)-[:REFERENCES]->(i)
        """

        params = {
            "repo": repo,
            "source_repo": source_repo,
            "issue_number": issue_number,
            "source_number": source_dict["number"],
            "title": source_dict.get("title"),
            "url": source_dict.get("url"),            
        }

    # CrossReferencedEvent
    #   source = Issue #81
    #
    # Graph: Issue #81 ──REFERENCES──> Issue #1

    elif source_type == "Issue":
        query = """
            MATCH (i:Issue {
                repo: $repo,
                number: $issue_number
            })

            MERGE (source:Issue {
                repo: $source_repo,
                number: $source_number
            })

            SET
                source.title = $title,
                source.url = $url

            MERGE (source)-[:REFERENCES]->(i)
        """

        params = {
            "repo": repo,
            "source_repo": source_repo,
            "issue_number": issue_number,
            "source_number": source_dict["number"],
            "title": source_dict.get("title"),
            "url": source_dict.get("url"),
        }

    else:
        raise ValueError(f"Unknown source_type: {source_type}")

    with driver.session() as session:
        session.run(query, **params)
        

