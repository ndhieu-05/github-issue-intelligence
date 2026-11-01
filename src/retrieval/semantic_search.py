"""Semantic search cho GitHub issues bằng embedding và pgvector."""

import logging
import re

import numpy as np
from pgvector.psycopg2 import register_vector

from src.embeddings.embedder import build_text, embed_texts
from src.retrieval.graph_expand import closed_by_for
from src.storage.postgres_client import get_connection

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 200  # số ký tự đầu của body đưa vào snippet


def _attach_closed_by(results: list[dict]) -> None:
    """Gắn trường closed_by vào từng kết quả
    """
    try:
        closers = closed_by_for([(r["repo"], r["number"]) for r in results])
    except Exception as exc:
        logger.warning("Không lấy được closed_by từ Neo4j: %s", exc)
        return

    for r in results:
        r["closed_by"] = closers.get((r["repo"], r["number"]), [])


def semantic_search(
    query_text: str,
    top_k: int = 5,
    exclude_number: int | None = None,
    include_closed_by: bool = True,
) -> list[dict]:
    """Tìm các GitHub issue có ngữ nghĩa gần nhất với query.

    Args:
        query_text: Nội dung truy vấn cần tìm kiếm.
        top_k: Số issue tối đa được trả về.
        exclude_number: Loại issue có số này khỏi kết quả,
            phục vụ đánh giá retrieval để tránh tìm thấy chính nó.
        include_closed_by: Gắn kèm PR/commit đã đóng từng issue (lấy từ Neo4j).
            Tham số này không nằm trong schema của tool: hệ thống đặt qua `overrides`
            (ví dụ tắt đi khi làm ablation), LLM không nhìn thấy.

    Returns:
        Danh sách dict, mỗi dict gồm repo, number, title, url, snippet, distance
        và (nếu include_closed_by) closed_by.
    """
    if not query_text or not query_text.strip():
        return []

    if top_k < 1:
        raise ValueError("top_k phải lớn hơn hoặc bằng 1")

    # Tạo embedding cho query theo cùng quy trình với corpus
    query = build_text(query_text)
    query_vector = np.asarray(embed_texts([query]))[0]

    # Truy vấn các issue gần nhất theo cosine distance
    sql = """
        SELECT
            r.owner || '/' || r.name AS repo,
            i.number,
            i.title,
            i.url,
            i.body,
            i.embedding <=> %s AS distance
        FROM issues AS i
        JOIN repos AS r ON i.repo_id = r.repo_id
        WHERE i.embedding IS NOT NULL
    """
    params: list = [query_vector]

    if exclude_number is not None:
        sql += " AND i.number <> %s"
        params.append(exclude_number)

    sql += """
        ORDER BY i.embedding <=> %s ASC
        LIMIT %s
    """
    params.extend([query_vector, top_k])

    # Giữ kết nối PostgreSQL càng ngắn càng tốt: đóng trước khi gọi Neo4j.
    conn = get_connection()
    try:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    # Chuyển kết quả DB thành dữ liệu dễ dùng cho agent
    results = []
    for repo, number, title, url, body, distance in rows:
        clean_body = re.sub(r"\s+", " ", body or "").strip()  # chuẩn hóa khoảng trắng, xuống dòng
        results.append(
            {
                "repo": repo,
                "number": number,
                "title": title,
                "url": url,
                "snippet": clean_body[:SNIPPET_CHARS],
                "distance": round(float(distance), 3),
            }
        )

    if include_closed_by and results:
        _attach_closed_by(results)

    return results