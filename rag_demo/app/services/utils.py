"""
公共服务工具函数
"""

import hashlib
import os


def compute_sha256(filepath: str) -> str:
    """
    计算文件 SHA-256（流式读取，避免大文件内存爆炸）

    Args:
        filepath: 文件路径

    Returns:
        64 字符十六进制 SHA-256 字符串
    """
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_sha256_bytes(data: bytes) -> str:
    """
    计算内存 bytes 的 SHA-256

    Args:
        data: 字节数据

    Returns:
        64 字符十六进制 SHA-256 字符串
    """
    return hashlib.sha256(data).hexdigest()


def clear_cache_dir(path: str):
    """
    清空缓存路径（文件或目录）

    Args:
        path: 缓存路径（文件或目录）
    """
    import shutil

    if os.path.exists(path):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            os.makedirs(path, exist_ok=True)
        else:
            os.remove(path)
