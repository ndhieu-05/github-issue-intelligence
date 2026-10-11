"""Semantic search cho GitHub issues bằng embedding và pgvector."""

import re

import numpy as np
import psycopg2
from pgvector.psycopg2 import register_vector

from src.embeddings.embedder import build_text, embed_texts
from src.storage.config import load_config

def semantic_search(
    query_text:str,
    top_k: int = 5,
    exclude_number: int | None = None,
)-> list[dict]:
    """Tìm các GitHub issue có ngữ nghĩa gần nhất với query.

    Args:
        query_text: Nội dung truy vấn cần tìm kiếm.
        top_k: Số issue tối đa được trả về.
        exclude_number: Loại issue có số này khỏi kết quả,
            phục vụ đánh giá retrieval để tránh tìm thấy chính nó.

    Returns:
        Danh sách dict, mỗi dict gồm repo, number, title, url,
        snippet và distance.
    """
    if not query_text or not query_text.strip():
        return []

    if top_k <1:
        raise ValueError("top_k phải lớn hơn hoặc bằng 1")

    # Tạo embedding cho query theo cùng quy trình với corpus
    query = build_text(query_text)
    query_vector = np.asarray(embed_texts([query]))[0]

    # Kết nối PostgreSQL và đăng ký kiểu vector
    config = load_config()
    db_config = config["postgres"]

    conn = psycopg2.connect(
        host=db_config["host"],
        dbname=db_config["dbname"],
        user=db_config["user"],
        password=db_config["password"],
        port=db_config["port"],
    )

    try:
        register_vector(conn)

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
        params = [query_vector]

        if exclude_number is not None:
            sql += " AND i.number <> %s"
            params.append(exclude_number)

        sql += """
            ORDER BY i.embedding <=> %s ASC
            LIMIT %s
        """
        params.extend([query_vector, top_k])

        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        # Chuyển kết quả DB thành dữ liệu dễ dùng cho agent
        results = []

        for repo, number, title, url, body, distance in rows:
           # Chuẩn hóa khoảng trắng và xuống dòng trong body.
            clean_body = re.sub(r"\s+", " ", body or "").strip()

            results.append(
                {
                    "repo": repo,
                    "number": number,
                    "title": title,
                    "url": url,
                    "snippet": clean_body[:200],
                    "distance": round(float(distance), 3),
                }
            )

        return results

    finally:
        conn.close()
           

    