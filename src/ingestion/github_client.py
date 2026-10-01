"""
Module kết nối và truy vấn GitHub GraphQL API.

Module này cung cấp hàm run_query() để gửi truy vấn GraphQL
đến GitHub và lấy phần dữ liệu (data) trong response.
"""

import os

import requests
from dotenv import load_dotenv # Thư viện đọc các biến môi trường từ file .env

# Cấu hình 
load_dotenv()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

def run_query(query: str, variables: dict | None = None) -> dict:
    """Gửi một truy vấn GraphQL đến GitHub GraphQL API.

    Args:
        query: Chuỗi truy vấn GraphQL cần gửi đến GitHub.
        variables: Các biến được truyền vào truy vấn GraphQL.
            Mặc định là None.

    Returns:
        dict: Phần "data" trong response trả về từ GitHub.

    Raises:
        requests.HTTPError: Nếu request HTTP đến GitHub thất bại.
        RuntimeError: Nếu GitHub trả về lỗi GraphQL.
    """

    # Kiểm tra Token
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN chưa được cấu hình")

    # Chuẩn bị headers
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }

    # Chuẩn bị body
    payload = {
        "query": query,
        "variables": variables or {}
    }

    # Gửi request
    response = requests.post(
        GITHUB_GRAPHQL_URL,
        headers = headers,
        json = payload,
        timeout = 30
    )

    # Kiểm tra lỗi HTTP
    response.raise_for_status()

    # Đọc response JSON
    result = response.json()

    # Kiểm tra lỗi GraphQL
    if "errors" in result:
        raise RuntimeError(result["errors"])

    # Trả về data
    return result["data"]