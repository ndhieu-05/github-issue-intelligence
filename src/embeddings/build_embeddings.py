"""
Pipeline tạo embedding cho GitHub issues và lưu vào PostgreSQL.

Đọc title và body từ bảng issues, ghép thành văn bản đầu vào,
sinh embedding theo từng batch và cập nhật vector vào cột embedding
tương ứng với mỗi issue_id.
"""

import numpy as np
from psycopg2.extras import execute_batch
from pgvector.psycopg2 import register_vector

from src.storage.config import load_config
from src.storage.postgres_client import get_connection
from src.embeddings.embedder import embed_texts, build_text

BATCH_SIZE = 64

def fetch_issues(conn) -> list[tuple[int, str, str|None]]:
    """
    Đọc (issue_id, title, body) của tất cả issue.
    """
    query = """
        SELECT 
            issue_id,
            title,
            body
        FROM 
            issues
        ORDER BY
            issue_id
    """

    with conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()

def update_embeddings(conn, issue_ids: list[int], vectors: np.ndarray)->None:
    """
    Ghi Vector vào cột embedding, rồi commit.
    """

    if len(issue_ids) != len(vectors):
        raise ValueError("Số issue_ids phải bằng số vectors")
        
    query = """
        UPDATE issues
        SET
            embedding = %s 
        WHERE issue_id = %s
    """

    with conn.cursor() as cur:
        execute_batch(
            cur,
            query,
            [
                (vector, issue_id)
                for issue_id, vector in zip(issue_ids, vectors)
            ],
            page_size= 100
        )

    conn.commit()

def main():
    config = load_config()
    dim = config["embedding"]["dim"]

    conn = get_connection()
    register_vector(conn) # dạy psycopg2 cách truyền numpy array vào cột vector

    try: 
        rows = fetch_issues(conn)
        print(f"Tổng số issue: {len(rows)}")

        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start: start + BATCH_SIZE]

            issue_ids = [row[0] for row in batch]
            texts = [
                build_text(title, body)
                for _, title, body in batch
            ]

            vectors = np.asarray(embed_texts(texts))

            if vectors.ndim != 2 or vectors.shape[1] != dim:
                raise ValueError(
                    f"Embedding sai số chiều: nhận được {vectors.shape}, "
                    f"cấu hình yêu cầu dim = {dim}"
                )

            update_embeddings(conn, issue_ids, vectors)

            processed = start + len(batch)
            print(f"Đã xử lý {processed}/{len(rows)} issues")

        print("Hoàn tất tạo và cập nhật embeddings!")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

if __name__ == "__main__":
    main()