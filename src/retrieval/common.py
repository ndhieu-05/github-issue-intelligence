ISSUE_KEYS = (
    "repo", "number", "title", "url", "state", "createdAt", "closedAt"
)
LINK_KEYS = ("repo", "number", "title", "url")
COMMIT_KEYS = ("repo", "oid", "url")

def _pick(
    props: dict|None,
    keys: tuple[str, ...],
)-> dict:
    """Chỉ lấy các thuộc tính được yêu cầu"""
    props = props or {}
    return {k: props.get(k) for k in keys}