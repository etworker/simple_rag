"""Retriever 抽象层单元测试 — 验证 FaissRetriever 行为与接口契约。"""

import os
import tempfile

import numpy as np
import pytest

from app.services.retriever import FaissRetriever, Retriever


def test_retriever_is_abstract():
    """Retriever 应有抽象方法，不可实例化"""
    with pytest.raises(TypeError):
        Retriever()  # type: ignore


def test_faiss_add_search():
    r = FaissRetriever()
    # 3 个向量：a 与 b 相近，c 远离
    a = np.array([[1.0, 0.0]], dtype=np.float32)
    b = np.array([[0.95, 0.31]], dtype=np.float32)
    c = np.array([[0.0, 1.0]], dtype=np.float32)
    r.add(np.vstack([a, b, c]))
    assert r.count == 3
    assert r.dim == 2

    scores, indices = r.search(np.array([[1.0, 0.0]], dtype=np.float32), top_k=2)
    assert len(indices) == 2
    assert indices[0] == 0  # 自身最相似
    assert scores[0] > scores[1]


def test_faiss_remove():
    r = FaissRetriever()
    r.add(np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]], dtype=np.float32))
    keep = np.array([True, False, True])  # 删掉 [0,1]（原 1 号）
    r.remove(keep)
    assert r.count == 2
    # remove 后重排为 [1,0]=0, [0.5,0.5]=1；原被删 [0,1] 不再可检索
    scores, indices = r.search(np.array([[0.0, 1.0]], dtype=np.float32), top_k=2)
    assert len(indices) == 2
    assert set(indices.tolist()) == {0, 1}  # 只返回保留的两个向量
    # 与 [0,1] 最相似的是 [0.5,0.5]（现 1 号）：内积 0.5 > [1,0] 的 0.0
    assert indices[0] == 1
    assert scores[0] > scores[1]


def test_faiss_save_load_roundtrip():
    r = FaissRetriever()
    r.add(np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32))
    with tempfile.TemporaryDirectory() as d:
        r.save(d)
        assert os.path.exists(os.path.join(d, "embeddings.npy"))

        r2 = FaissRetriever()
        r2.load(d)
        assert r2.count == 2
        scores, indices = r2.search(np.array([[1.0, 0.0]], dtype=np.float32), top_k=1)
        assert indices[0] == 0


def test_faiss_clear():
    r = FaissRetriever()
    r.add(np.array([[1.0, 0.0]], dtype=np.float32))
    assert r.count == 1
    r.clear()
    assert r.count == 0
    scores, indices = r.search(np.array([[1.0, 0.0]], dtype=np.float32), top_k=1)
    assert len(indices) == 0


def test_faiss_load_missing_dir():
    r = FaissRetriever()
    r.load("/nonexistent/dir")
    assert r.count == 0
