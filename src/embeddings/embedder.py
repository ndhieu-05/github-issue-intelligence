"""Tạo vector embedding đã chuẩn hóa bằng Sentence Transformers."""

import numpy as np
from sentence_transformers import SentenceTransformer

from src.storage.config import load_config

config = load_config()

MODEL_NAME = config["embedding"]["model"]
model = SentenceTransformer(MODEL_NAME)

def embed_texts(texts: list[str]) -> np.ndarray:
    """Chuyển danh sách văn bản thành ma trận embedding đã chuẩn hóa.

    Args:
        texts: Danh sách chuỗi cần chuyển thành vector.

    Returns:
        np.ndarray: shape (len(texts), dim), dtype float32, đã chuẩn hóa L2
        nên cosine distance tính đúng. dim lấy từ config["embedding"]["dim"].
    """
    return model.encode(texts, batch_size=32, normalize_embeddings=True)


def build_text(title: str, body: str | None = None) -> str:
    """Ghép tiêu đề và nội dung issue thành văn bản dùng để embedding."""
    return f"{title or ''}\n\n{body or ''}".strip()
