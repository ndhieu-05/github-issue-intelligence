"""Tool graph_expand: mở rộng một issue thành các bằng chứng liên quan trong Neo4j.

Tool nhận diện một issue theo repository và số hiệu, sau đó truy xuất
các quan hệ liên kết để cung cấp thêm ngữ cảnh cho quá trình suy luận.

Các thông tin được truy xuất gồm:

+ CLOSED_BY: PR hoặc commit liên quan đến việc đóng issue.

+ INCLUDES: các commit thuộc PR, dùng để bổ sung bằng chứng thay đổi mã nguồn.

+ Co-closed issues: các issue khác được đóng bởi cùng một PR.

+ REFERENCES: các issue hoặc PR có quan hệ tham chiếu đến issue đầu vào.

Kết quả được chuẩn hóa thành dictionary, giới hạn số phần tử trong từng
danh sách và giữ lại số lượng phần tử thực tế trong trường counts.

Tool chỉ mở rộng cấu trúc và quan hệ trong graph, không tự xác minh tính
đúng đắn của bằng chứng hoặc thực hiện suy luận ngữ nghĩa.
"""

from src.storage.neo4j_client import close_driver, get_driver
from src.retrieval.common import ISSUE_KEYS, LINK_KEYS, COMMIT_KEYS, _pick

# Truy vấn 1: issue + thứ đã đóng nó + commit của PR đó
CLOSED_BY_QUERY = """
    MATCH (i: Issue{
        repo: $repo,
        number: $number    
    })
    OPTIONAL MATCH (i)-[:CLOSED_BY]->(x)
    OPTIONAL MATCH (x)-[:INCLUDES]->(c: Commit)

    RETURN 
        i,
        x,
        labels(x)[0] AS type,
        collect(c) AS commits
    ORDER BY type, x.number
"""

# Truy vấn 2: Tìm các issue khác được đóng bởi cùng 1 PR với issue đầu vào
CO_CLOSED_QUERY = """
    MATCH (i: Issue{
        repo: $repo,
        number: $number    
    })-[:CLOSED_BY]->(pr: PullRequest)
    <-[:CLOSED_BY]-(other: Issue)
    WHERE other <> i
    RETURN DISTINCT other
    ORDER BY other.number
"""

# Truy vấn 3: PR/ issue nhắc tới issue này
REFERENCED_BY_QUERY = """
    MATCH (src)-[:REFERENCES]->(i:Issue {repo: $repo, number: $number})
    RETURN DISTINCT
        src,
        labels(src)[0] AS type
    ORDER BY src.number
"""

def graph_expand(
    repo:str,
    number: int,
    max_items: int = 10
)-> dict|None:
    """Mở rộng một issue sang các bằng chứng liên kết trong graph.

    Args:
        repo: "owner/name" của issue, lấy từ kết quả semantic_search.
        number: số issue.
        max_items: số phần tử tối đa của mỗi danh sách. Tổng số thật vẫn được ghi trong "counts".

    Returns:
        None nếu không tìm thấy issue trong graph. Ngược lại là dict gồm:
        - issue: thông tin issue
        - closed_by: PR/Commit đã đóng issue. PR kèm
          commit_count và danh sách commits.
        - co_closed_issues: issue khác bị đóng bởi cùng PR.
        - referenced_by: PR/issue nhắc tới issue này.
        - counts: tổng số thật của từng danh sách trước khi cắt.
    """

    driver = get_driver()

    try: 
        with driver.session() as session:
            closed_rows = session.run(
                CLOSED_BY_QUERY,
                repo = repo,
                number = number
            ).data() # chuyển các node và relationship thành dữ liệu thuộc tính dạng dictionary 
                     # và đảm bảo tiêu thụ dữ liệu khi session chưa đóng

            if not closed_rows:
                return None # MATCH issue không khớp thì không có dòng nào

            co_rows = session.run(
                CO_CLOSED_QUERY,
                repo = repo,
                number = number
            ).data()

            ref_rows = session.run(
                REFERENCED_BY_QUERY,
                repo = repo,
                number = number
            ).data()

    finally:
        close_driver(driver)

    issue = _pick(closed_rows[0]["i"], ISSUE_KEYS)

    closed_by = []

    for row in closed_rows:
        x = row["x"]
        if x is None:
            continue
        if row["type"] == "Commit":
            closed_by.append(
                {
                    "type": "Commit",
                    **_pick(x, COMMIT_KEYS),
                }
            )
        else:
            commits = [_pick(c, COMMIT_KEYS) for c in row["commits"]]
            closed_by.append(
                {
                    "type": row["type"],
                    **_pick(x, LINK_KEYS),
                    "commit_count": len(commits),
                    "commits": commits[:max_items],
                }
            )

    co_closed = [_pick(row["other"], LINK_KEYS) for row in co_rows]
    referenced_by = [
        {
            "type": row["type"],
            **_pick(row["src"], LINK_KEYS),
        }
        for row in ref_rows
    ]

    counts = {
        "closed_by": len(closed_by),
        "co_closed_issues": len(co_closed),
        "referenced_by": len(referenced_by),
    }

    return {
        "issue": issue,
        "closed_by": closed_by[:max_items],
        "co_closed_issues": co_closed[:max_items],
        "referenced_by": referenced_by[:max_items],
        "counts": counts,
    }