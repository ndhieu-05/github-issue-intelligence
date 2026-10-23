"""Tool fetch_details: lấy nội dung chi tiết của issue, pull request hoặc commit.
 
Nguồn dữ liệu (Option A trong design-decisions.md):
- Issue: PostgreSQL (body + comments). Nếu không có trong PostgreSQL thì
  fallback về node trong Neo4j, khi đó chỉ có title/url.
- Pull request, commit: Neo4j (nội dung diff chưa được ingest).
"""

import logging
from typing import Any
 
from src.storage.neo4j_client import close_driver, get_driver
from src.storage.postgres_client import get_connection
from src.retrieval.common import ISSUE_KEYS, LINK_KEYS, COMMIT_KEYS, _pick

logger = logging.getLogger(__name__)

BODY_LIMIT = 3000
COMMENT_BODY_LIMIT = 1000
MAX_COMMENTS = 5
COMMENTS_HEAD = 2 # khi quá nhiều comment: giữ 2 comment đầu + (MAX_COMMENTS - 2) comment cuối
MAX_LIST_ITEMS = 10


NOTE_ISSUE_GRAPH_ONLY = (
    "Chỉ có tiêu đề và liên kết; nội dung đầy đủ của issue này chưa được ingest."
)
NOTE_PR_GRAPH_ONLY = (
    "Chỉ có thông tin từ graph (tiêu đề, liên kết, commit, issue được đóng); "
    "nội dung mô tả và diff của PR chưa được ingest."
)
NOTE_COMMIT_GRAPH_ONLY = (
    "Chỉ có mã commit, liên kết và PR chứa nó; nội dung thay đổi chưa được ingest."
)
NOTE_PR_NO_DETAILS = (
    "Chỉ có tiêu đề và liên kết. Commit và nội dung PR chưa được ingest cho PR này "
    "(chỉ PR đã đóng một issue trong dữ liệu mới có commit), nên commit hoặc closes_issues "
    "trống không có nghĩa là PR không có commit hay không đóng issue nào."
)

# ======= PostgreSQL
ISSUE_SQL = """
    SELECT
        i.issue_id,
        i.title,
        i.body,
        i.state,
        i.created_at,
        i.closed_at,
        i.url
    FROM issues AS i
    JOIN repos AS r 
    ON r.repo_id = i.repo_id
    WHERE r.owner = %s AND r.name = %s AND i.number = %s
"""

COMMENTS_SQL = """
    SELECT 
        author_login,
        body,
        created_at
    FROM comments
    WHERE issue_id = %s
    ORDER BY created_at, comment_id
"""

# ====== Cypher Queries
# Query 1: Lấy issue từ graph để fallback khi PostgreSQL không có.
ISSUE_GRAPH_QUERY = """
    MATCH (i:Issue{
        repo: $repo,
        number: $number
    })
    RETURN i
"""

# Query 2: Lấy PR, commits và các issue được PR đóng
PULL_REQUEST_QUERRY = """
    MATCH (pr:PullRequest{
        repo: $repo,
        number: $number
    })

    OPTIONAL MATCH (pr)-[:INCLUDES]->(c:Commit)
    WITH pr, collect(DISTINCT c) AS commits

    OPTIONAL MATCH (i:Issue)-[:CLOSED_BY]->(pr)
    WITH pr, commits, collect(DISTINCT i) AS issues

    RETURN 
        pr,
        commits,
        issues
"""

# Query 3: Lấy commit và các PR chứa commit đó
COMMIT_QUERY = """
    MATCH (c:Commit{
        repo: $repo,
        oid: $oid
    })

    OPTIONAL MATCH (pr:PullRequest)-[:INCLUDES]->(c)

    RETURN
        c,
        collect(DISTINCT pr) AS pull_requests
"""

# ====== Các hàm helper
def _to_iso(value: Any) -> str|None:
    """Chuyển datetime/date thành chuỗi JSON-serializable."""
    if value is None:
        return None

    if hasattr(value, "isoformat"):
        return value.isoformat()

    return value

def _truncate(
    value: str|None,
    limit: int,
)-> tuple[str | None, bool]:
    """Cắt chuỗi nếu vượt giới hạn; trả về (giá trị, có_bị_cắt)."""
    if value is None or len(value) <= limit:
        return value, False

    return value[:limit], True

def _node_props(node: dict | None)-> dict:
    """Chuẩn hóa node đã được Neo4j Result.data() chuyển thành dict."""
    return node or {}

def _run_graph_query(query: str, **params) -> list[dict]:
    """Chạy Cypher và lấy dữ liệu khi session còn mở"""
    driver = get_driver()

    try:
        with driver.session() as session:
            return session.run(query, **params).data()
    finally:
        close_driver(driver)

def _select_comments(rows: list[tuple]) -> tuple[list[tuple], int]:
    """Chọn comment đưa cho LLM.
 
    Với issue đã đóng, lời giải thích cách sửa thường nằm ở cuối, nên khi có
    nhiều hơn MAX_COMMENTS comment thì giữ COMMENTS_HEAD comment đầu và phần
    còn lại lấy từ cuối.
 
    Returns:
        (các comment được chọn, số comment bị bỏ qua)
    """
    total = len(rows)
    if total <= MAX_COMMENTS:
        return rows, 0

    tail = MAX_COMMENTS - COMMENTS_HEAD # luôn > 0 với các hằng số hiện tại
    return rows[:COMMENTS_HEAD] + rows[-tail:],  total - MAX_COMMENTS

# ====== 1. Issue
def fetch_issue_from_postgres(
    repo: str,
    number: int
)-> dict|None:
    """Lấy issue, body và comments từ PostgreSQL.
 
    Args:
        repo: "owner/name".
        number: số issue.
 
    Returns:
        Dict nội dung issue (nguồn "postgres"), hoặc None nếu không có dòng nào.
    """
    owner, name = repo.split("/", 1)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(ISSUE_SQL, (owner, name, number))
            issue_row = cur.fetchone()
            if issue_row is None:
                return None

            issue_id, title, body, state, created_at, closed_at, url = issue_row

            cur.execute(COMMENTS_SQL, (issue_id,))
            comment_rows = cur.fetchall()

    finally:
        conn.close()

    body_text, any_truncated = _truncate(body, BODY_LIMIT)
    shown, omitted = _select_comments(comment_rows)

    comments = []
    for author, comment_body, comment_created_at in shown:
        text, cut = _truncate(comment_body, COMMENT_BODY_LIMIT)
        any_truncated = any_truncated or cut
        comments.append(
            {
                "author": author,
                "createdAt": _to_iso(comment_created_at),
                "body": text,
            }
        )

    return {
        "kind": "issue",
        "repo": repo,
        "number": number,
        "title": title,
        "url": url,
        "state": state,
        "createdAt": _to_iso(created_at),
        "closedAt": _to_iso(closed_at),
        "body": body_text,
        "comment_count": len(comment_rows),
        "comments": comments,
        "omitted_comments": omitted,
        "truncated": any_truncated,
        "source": "postgres",        
    }

def fetch_issue_from_graph(
    repo: str,
    number: int
)-> dict|None:
    rows = _run_graph_query(ISSUE_GRAPH_QUERY, repo = repo, number = number)
    if not rows:
        return None

    props = _node_props(rows[0]["i"])
    return {
        "kind": "issue",
        **_pick(props, ISSUE_KEYS),
        "source": "graph_only",
        "note": NOTE_ISSUE_GRAPH_ONLY,
    }

def fetch_issue(repo: str, number: int) -> dict|None:
    """Issue: ưu tiên PostgreSQL, không có thì fallback sang Neo4j."""
    result = fetch_issue_from_postgres(repo, number)
    if result is not None:
        return result

    logger.debug("Issue %s#%s không có trong PostgreSQL, fallback sang Neo4j", repo, number)
    return fetch_issue_from_graph(repo, number)

# ======= 2. Pull Request
def fetch_pull_request(
    repo: str,
    number: int
)-> dict|None:
    """Lấy PR, commit của nó và các issue nó đã đóng từ Neo4j."""
    rows = _run_graph_query(PULL_REQUEST_QUERRY, repo = repo, number = number)
    if not rows: 
        return None

    row = rows[0]
    commits = [_commit_props(c) for c in row["commits"]]
    issues = [_pick(i, LINK_KEYS) for i in row["issues"]]
    has_commits = bool(commits)

    return {
        "kind": "pull_request",
        **_pick(_node_props(row["pr"]), LINK_KEYS),
        "commit_count": len(commits) if has_commits else None,
        "commits": commits[:MAX_LIST_ITEMS] if has_commits else None,
        "closes_issue_count": len(issues),
        "closes_issues": issues[:MAX_LIST_ITEMS],
        "source": "graph_only",
        "note": NOTE_PR_GRAPH_ONLY if has_commits else NOTE_PR_NO_DETAILS,
    }

# ====== 3. Commit
def fetch_commit(
    repo: str,
    oid: str
)-> dict | None:
    """Lấy commit và các PR chứa nó từ Neo4j."""
    rows = _run_graph_query(COMMIT_QUERY, repo = repo, oid = oid)
    if not rows:
        return None

    row = rows[0]
    pull_requests = [_pick(p, LINK_KEYS) for p in row["pull_requests"]]

    return {
        "kind": "commit",
        **_commit_props(row["c"]),
        "in_pull_requests": pull_requests[:MAX_LIST_ITEMS],
        "source": "graph_only",
        "note": NOTE_COMMIT_GRAPH_ONLY,
    }


# ======= Tool
def fetch_details(kind: str, repo: str, ref: int|str)-> dict|None:
    """Lấy nội dung chi tiết của một issue, pull request hoặc commit.
 
    Args:
        kind: "issue", "pull_request" hoặc "commit".
        repo: "owner/name", lấy từ kết quả của semantic_search/graph_expand.
        ref: số hiệu (issue, pull_request) hoặc mã oid đầy đủ 40 ký tự (commit).
            Chuỗi dạng "45" hoặc "#45" cũng được chấp nhận cho số hiệu.
 
    Returns:
        Dict nội dung. Trường "source" cho biết nguồn ("postgres" hoặc
        "graph_only"); với "graph_only" có thêm "note" nói rõ phần nào còn thiếu.
        None nếu không tìm thấy ở đâu cả.
 
    Raises:
        ValueError: nếu kind không hợp lệ hoặc ref không phải số với issue/PR.
    """
    if kind in ("issue", "pull_request"):
        try:
            number = int(str(ref).lstrip("#").strip())
        except ValueError:
            raise ValueError(f"ref phải là số với kind = {kind!r}, nhận được: {ref!r}")

        if kind == "issue":
            return fetch_issue(repo, number)
        return fetch_pull_request(repo, number)

    if kind == "commit":
        return fetch_commit(repo, str(ref).strip())

    raise ValueError(f"kind không hợp lệ: {kind!r} (chọn issue, pull_request hoặc commit)")

def _commit_props(node: dict | None) -> dict:
    props = _node_props(node)
    result = _pick(props, COMMIT_KEYS)
    result["message"] = props.get("messageHeadline")
    return result