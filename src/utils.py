def parse_repo_from_url(url: str) -> str:
    """Trích "owner/name" từ URL GitHub của PR/Issue/Commit.

    Args:
        url: URL dạng "https://github.com/<owner>/<name>/pull/64",
            ".../issues/81" hoặc ".../commit/<oid>".

    Returns:
        str: "<owner>/<name>", ví dụ "openbao/go-kms-wrapping".
    """
    parts = url.split("/")
    return f"{parts[3]}/{parts[4]}"