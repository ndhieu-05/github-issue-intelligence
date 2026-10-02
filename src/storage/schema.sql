CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE repos (
    repo_id     SERIAL PRIMARY KEY,
    owner       TEXT NOT NULL,
    name        TEXT NOT NULL,
    UNIQUE (owner, name)
);

CREATE TABLE issues (
    issue_id    SERIAL PRIMARY KEY,
    repo_id     INT NOT NULL REFERENCES repos(repo_id),
    number      INT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT,
    state       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    closed_at   TIMESTAMPTZ,
    url         TEXT NOT NULL,
    embedding   VECTOR(384),          -- đổi số chiều theo model embedding bạn chọn tuần sau
    UNIQUE (repo_id, number)          -- khóa tự nhiên: KHÔNG dùng number một mình
);

CREATE TABLE comments (
    comment_id   SERIAL PRIMARY KEY,
    issue_id     INT NOT NULL REFERENCES issues(issue_id),
    author_login TEXT,
    body         TEXT,
    created_at   TIMESTAMPTZ NOT NULL
);