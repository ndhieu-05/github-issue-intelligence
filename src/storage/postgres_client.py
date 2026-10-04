from pathlib import Path

import psycopg2
from src.storage.config import load_config

# Config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# postgres connection
def get_connection():
    """
    Kết nối với PostgreSQL
    """
    config = load_config()
    postgres_config = config["postgres"]

    conn = psycopg2.connect(
        host=postgres_config["host"],
        dbname=postgres_config["dbname"],
        user=postgres_config["user"],
        password=postgres_config["password"],
        port=postgres_config["port"],
    )

    return conn

# SCHEMA
def create_schema(conn):
    """
    Tạo bảng từ schema.sql
    """
    with open(SCHEMA_PATH, "r", encoding = "utf-8") as f:
        schema_sql = f.read()

    with conn.cursor() as cur:
        cur.execute(schema_sql)

    conn.commit()


# Repository
def upsert_repo(conn, owner, name):
    """
    Insert dữ liệu vào bảng Repo
    return:
        repo_id(int)
    """
    query = """
        INSERT INTO repos(owner, name)
        VALUES (%s, %s)
        ON CONFLICT (owner, name)
        DO UPDATE SET owner = EXCLUDED.owner
        RETURNING repo_id;  
    """

    with conn.cursor() as cur:
        cur.execute(query, (owner, name))
        repo_id = cur.fetchone()[0]

    conn.commit()

    return repo_id

# Issue
def upsert_issue(conn, repo_id, issue_dict):
    """
    Insert dữ liệu vào bảng Issue

    Return:
        issue_id (int)
    """

    query = """
        INSERT INTO issues (
            repo_id,
            number,
            title,
            body,
            state,
            created_at,
            closed_at,
            url
        )
        VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (repo_id, number)
        DO UPDATE SET
            title = EXCLUDED.title,
            body = EXCLUDED.body,
            state = EXCLUDED.state,
            created_at = EXCLUDED.created_at,
            closed_at = EXCLUDED.closed_at,
            url = EXCLUDED.url
        RETURNING issue_id;
    """

    values = (
        repo_id,
        issue_dict["number"],
        issue_dict["title"],
        issue_dict.get("body"),
        issue_dict["state"],
        issue_dict["createdAt"],
        issue_dict.get("closedAt"),
        issue_dict["url"],
    )

    with conn.cursor() as cur:
        cur.execute(query, values)
        issue_id = cur.fetchone()[0]

    conn.commit()

    return issue_id

# Comments
def insert_comments(conn, issue_id, comments_list):
    """
    Insert comments vào từng issue
    """

    if not comments_list:
        return

    query = """
        INSERT INTO comments (
            issue_id,
            author_login,
            body,
            created_at
        )
        VALUES (%s, %s, %s, %s);
    """

    values = [
        (
            issue_id,
            comment.get("author", {}).get("login")
            if comment.get("author")
            else None,
            comment.get("body"),
            comment.get("createdAt"),
        )
        for comment in comments_list
    ]

    with conn.cursor() as cur:
        cur.executemany(query, values)

    conn.commit()