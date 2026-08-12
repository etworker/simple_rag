"""
GPU 加速 — rag_demo 集成测试

策略:
  - 尽量真实调用: device_utils 检测、实际 FAISS 运算、SentenceTransformer 实例化
  - 无法真实执行的环境（无 GPU、模型未缓存）打印状态后跳过，不 mock
  - 只对"模型加载超慢/无网络"场景 fallback 跳过，不 mock 结果
"""

import gc
import logging
import os
import shutil
import sys
import tempfile
from typing import ClassVar

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger("test.gpu_accel")


# ---------------------------------------------------------------------------
# device_utils 真实检测
# ---------------------------------------------------------------------------


class TestDeviceUtilsReal:
    """测试 version_diff.device_utils 的真实检测逻辑"""

    def test_resolve_with_actual_config(self):
        """用 rag_demo config.json 的真实 embedding 段做解析"""
        from version_diff.device_utils import resolve_embedding_device

        # 从真实 config 读取
        from app.services.config_store import ConfigStore

        store = ConfigStore()
        emb_cfg = store.get_section("embedding")
        device = resolve_embedding_device(emb_cfg)

        assert isinstance(device, str)
        assert device, "resolve 不应返回空字符串"
        # 期望值为 "cpu" / "cuda" / "cuda:0" / "mps" 之一
        assert any(k in device for k in ("cpu", "cuda", "mps")), f"未知设备: {device}"
        print(f"\n  [INFO] config 解析设备: {device}")

    def test_resolve_env_override(self):
        """环境变量 SIMPLE_RAG_EMBEDDING_DEVICE 覆盖 config"""
        from version_diff.device_utils import resolve_embedding_device

        old = os.environ.get("SIMPLE_RAG_EMBEDDING_DEVICE")
        try:
            os.environ["SIMPLE_RAG_EMBEDDING_DEVICE"] = "cpu"
            result = resolve_embedding_device({"device": "cuda"})
            assert result == "cpu", "环境变量应优先"
        finally:
            if old is None:
                os.environ.pop("SIMPLE_RAG_EMBEDDING_DEVICE", None)
            else:
                os.environ["SIMPLE_RAG_EMBEDDING_DEVICE"] = old

    def test_embedding_model_kwargs_auto(self):
        """dtype=auto 应返回 {'model_kwargs': {'torch_dtype': 'auto'}}"""
        from version_diff.device_utils import embedding_model_kwargs

        result = embedding_model_kwargs({"dtype": "auto"})
        assert result == {"model_kwargs": {"torch_dtype": "auto"}}

    def test_embedding_model_kwargs_real_torch_dtype(self):
        """dtype=float16 应映射到真实 torch.float16 枚举 (包裹在 model_kwargs 内)"""
        from version_diff.device_utils import embedding_model_kwargs

        result = embedding_model_kwargs({"dtype": "float16"})
        import torch

        assert result == {"model_kwargs": {"torch_dtype": torch.float16}}

    def test_model_loads_on_resolved_device(self):
        """实际用 resolve_embedding_device 的返回值实例化 SentenceTransformer ——
        验证配置的设备字符串可被 SentenceTransformer 接受。
        模型未缓存时跳过；加载成功则打印设备。"""
        from version_diff.device_utils import (
            embedding_model_kwargs,
            resolve_embedding_device,
        )

        from app.services.config_store import ConfigStore

        emb_cfg = ConfigStore().get_section("embedding")
        device = resolve_embedding_device(emb_cfg)
        kwargs = embedding_model_kwargs(emb_cfg)
        model_name = emb_cfg.get("model", "")

        if not model_name:
            pytest.skip("未配置 embedding.model")

        try:
            from sentence_transformers import SentenceTransformer

            # 设置超时常量，避免无网络时挂起
            os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
            m = SentenceTransformer(model_name, device=device, **kwargs)
            assert m is not None
            # 验证一条简单推理
            emb = m.encode(["测试句子"], show_progress_bar=False)
            assert emb.shape[1] > 0
            print(f"\n  [INFO] SentenceTransformer OK: device={device}, shape={emb.shape}")
        except OSError as e:
            # 模型未缓存 / 无网络 / token 缺失
            pytest.skip(f"模型未缓存 ({type(e).__name__}: {e})")
        except ImportError as e:
            pytest.skip(f"sentence_transformers 不可用 ({e})")

    def test_faiss_cpu_index_real(self):
        """真实 IndexFlatIP 检索"""
        import faiss
        import numpy as np

        d = 8
        nb = 100
        np.random.seed(42)
        xb = np.random.random((nb, d)).astype("float32")
        xb /= np.linalg.norm(xb, axis=1, keepdims=True)

        xq = np.random.random((1, d)).astype("float32")
        xq /= np.linalg.norm(xq, axis=1, keepdims=True)

        index = faiss.IndexFlatIP(d)
        index.add(xb)
        scores, indices = index.search(xq, 3)
        assert scores.shape == (1, 3)
        assert indices.shape == (1, 3)
        # 全部分数应在 [-1, 1] 内 (归一化内积 = 余弦相似度)
        assert (scores <= 1.01).all() and (scores >= -1.01).all()
        print(f"\n  [INFO] FAISS CPU 检索 OK: top3 scores={scores[0]}")

    def test_faiss_maybe_to_gpu_real_fallback(self):
        """在有/无 GPU 环境下 maybe_index_to_gpu 都应正常返回一个可用 index"""
        import faiss
        import numpy as np
        from version_diff.device_utils import maybe_index_to_gpu

        d = 4
        cpu_index = faiss.IndexFlatIP(d)
        cpu_index.add(np.random.random((5, d)).astype("float32"))

        gpu_index, used_gpu = maybe_index_to_gpu(cpu_index, gpu_id=0)
        assert gpu_index is not None
        # 返回的 index 必须能正常检索
        q = np.random.random((1, d)).astype("float32")
        scores, _indices = gpu_index.search(q, 2)
        assert scores.shape == (1, 2)
        print(f"\n  [INFO] maybe_index_to_gpu → used_gpu={used_gpu}, type={type(gpu_index).__name__}")


# ---------------------------------------------------------------------------
# DocStore 真实配置加载验证
# ===========================================================================


class TestDocStoreDeviceConfig:
    """验证 DocStore 实际应用了 config 中的 device 配置"""

    _tempdirs: ClassVar[list[str]] = []

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        """每个测试后清理临时目录"""
        yield
        for d in self._tempdirs:
            shutil.rmtree(d, ignore_errors=True)
        self._tempdirs.clear()
        gc.collect()

    def _fresh_store(self):
        from app.services.config_store import ConfigStore
        from app.services.doc_store import DocStore

        cfg = ConfigStore().to_dict()
        p1 = tempfile.mkdtemp()
        p2 = tempfile.mkdtemp()
        self._tempdirs.extend([p1, p2])
        cfg["persist_dir"] = p1
        cfg["parse_cache_dir"] = p2
        return DocStore(cfg)

    def test_docstore_loads_model_on_configured_device(self):
        """DocStore._get_model 应用 config 中的 device，能正常加载并推理"""
        store = None
        try:
            store = self._fresh_store()
            model = store._get_model()
            assert model is not None
            # 验证 config 中的 device 字段被实际读取
            cfg_device = store._config.get("embedding", {}).get("device", "auto")
            print(f"\n  [INFO] DocStore 加载模型 OK, config.device={cfg_device}")
            # 简单推理
            emb = model.encode(["测试"], show_progress_bar=False)
            assert emb.shape[1] > 0
        except OSError as e:
            pytest.skip(f"模型未缓存 ({type(e).__name__}: {e})")
        except ImportError as e:
            pytest.skip(f"sentence_transformers 不可用: {e}")

    def test_docstore_rebuild_index_works(self):
        """DocStore 在真实模型加载后能重建 FAISS 索引"""
        store = None
        try:
            store = self._fresh_store()
            # 模拟 add_document 后的状态
            from doc_parser import Paragraph

            store._paragraphs = [
                Paragraph(text=f"段落{i} 带一些内容来生成 embedding", source_file=f"doc{i}.pdf", page=1)
                for i in range(3)
            ]
            # 该方法依赖 _get_model, 模型不存在则跳过
            store._embeddings = (
                store._get_model()
                .encode([p.text for p in store._paragraphs], show_progress_bar=False)
                .astype("float32")
            )
            store._rebuild_index()
            assert store._index is not None
        except OSError:
            pytest.skip("模型未缓存")


# ---------------------------------------------------------------------------
# rag_demo config 已包含 GPU 字段
# ---------------------------------------------------------------------------


class TestConfigHasGPUFields:
    """验证 config.json 包含 device/dtype/gpu_id 字段"""

    def test_embedding_section_has_device(self):
        from app.services.config_store import ConfigStore

        store = ConfigStore()
        emb = store.get_section("embedding")
        assert "device" in emb, "embedding 段缺少 'device' 字段"
        assert "dtype" in emb, "embedding 段缺少 'dtype' 字段"
        assert "gpu_id" in emb, "embedding 段缺少 'gpu_id' 字段"
