"""
单测 - GPU/CPU 设备选择工具

测试策略：Mock torch.cuda 相关 API，无需实际 GPU。
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from version_diff import device_utils
from version_diff.device_utils import (
    _normalize_device_str,
    embedding_model_kwargs,
    maybe_index_to_gpu,
    resolve_embedding_device,
)


def _mock_torch(*, cuda=False, mps=False):
    """构造一个 Mock torch 模块, 支持指定 cuda/mps 是否可用。"""
    mock = MagicMock()
    mock.cuda.is_available.return_value = cuda
    if mps:
        mock_mps = MagicMock()
        mock_mps.is_available.return_value = True
        mock.backends.mps = mock_mps
    else:
        # 让 hasattr(torch.backends, "mps") 返回 False
        del mock.backends.mps
    return mock


# ---------------------------------------------------------------------------
# _normalize_device_str
# ---------------------------------------------------------------------------


class TestNormalizeDeviceStr:
    def test_auto_with_cuda(self):
        mock = _mock_torch(cuda=True)
        with patch.dict("sys.modules", {"torch": mock}):
            assert _normalize_device_str("auto") == "cuda"

    def test_auto_without_cuda_has_mps(self):
        mock = _mock_torch(mps=True)
        with patch.dict("sys.modules", {"torch": mock}):
            assert _normalize_device_str("auto") == "mps"

    def test_auto_without_any_gpu(self):
        mock = _mock_torch()
        with patch.dict("sys.modules", {"torch": mock}):
            assert _normalize_device_str("auto") == "cpu"

    def test_force_cpu_does_not_call_torch(self):
        """明确指定 cpu 时不调用 torch"""
        # 不 mock torch，若 torch 缺失不影响 cpu 路径
        assert _normalize_device_str("cpu") == "cpu"

    def test_cuda_fallback_to_cpu_when_unavailable(self):
        mock = _mock_torch()
        with patch.dict("sys.modules", {"torch": mock}):
            assert _normalize_device_str("cuda") == "cpu"
            assert _normalize_device_str("cuda:0") == "cpu"

    def test_cuda_accepted_when_available(self):
        mock = _mock_torch(cuda=True)
        with patch.dict("sys.modules", {"torch": mock}):
            assert _normalize_device_str("cuda") == "cuda"
            assert _normalize_device_str("cuda:0") == "cuda:0"

    def test_gpu_synonym_for_auto(self):
        mock = _mock_torch(cuda=True)
        with patch.dict("sys.modules", {"torch": mock}):
            assert _normalize_device_str("gpu") == "cuda"

    def test_empty_string_defaults_to_auto(self):
        mock = _mock_torch()
        with patch.dict("sys.modules", {"torch": mock}):
            assert _normalize_device_str("") == "cpu"

    def test_case_insensitive_cpu(self):
        """大小写不敏感 - cpu 路径"""
        assert _normalize_device_str("CPU") == "cpu"
        assert _normalize_device_str("Cpu") == "cpu"

    def test_case_insensitive_auto_with_mock(self):
        """大小写不敏感 - Auto 走 auto 路径"""
        mock = _mock_torch(cuda=True)
        with patch.dict("sys.modules", {"torch": mock}):
            assert _normalize_device_str("Auto") == "cuda"


# ---------------------------------------------------------------------------
# resolve_embedding_device
# ---------------------------------------------------------------------------


class TestResolveEmbeddingDevice:
    def setup_method(self):
        # 清除可能干扰的环境变量
        os.environ.pop("SIMPLE_RAG_EMBEDDING_DEVICE", None)

    def teardown_method(self):
        os.environ.pop("SIMPLE_RAG_EMBEDDING_DEVICE", None)

    def test_env_var_overrides_config(self):
        with patch.dict(os.environ, {"SIMPLE_RAG_EMBEDDING_DEVICE": "cpu"}):
            result = resolve_embedding_device({"device": "cuda"})
            assert result == "cpu"

    def test_config_dict_device_field(self):
        result = resolve_embedding_device({"device": "cpu"})
        assert result == "cpu"

    def test_config_string_is_device(self):
        result = resolve_embedding_device("cpu")
        assert result == "cpu"

    def test_none_config_defaults_auto(self):
        mock = _mock_torch(cuda=True)
        with patch.dict("sys.modules", {"torch": mock}):
            result = resolve_embedding_device(None)
            assert result == "cuda"

    def test_env_var_auto_expands(self):
        mock = _mock_torch()
        with (
            patch.dict("sys.modules", {"torch": mock}),
            patch.dict(os.environ, {"SIMPLE_RAG_EMBEDDING_DEVICE": "auto"}),
        ):
            result = resolve_embedding_device({})
            assert result == "cpu"


# ---------------------------------------------------------------------------
# embedding_model_kwargs
# ---------------------------------------------------------------------------


class TestEmbeddingModelKwargs:
    def test_empty_dict_returns_empty(self):
        assert embedding_model_kwargs({}) == {}

    def test_none_returns_empty(self):
        assert embedding_model_kwargs(None) == {}

    def test_dtype_auto(self):
        assert embedding_model_kwargs({"dtype": "auto"}) == {"model_kwargs": {"torch_dtype": "auto"}}

    def test_dtype_float16(self):
        expected = self._expected_dtype("float16")
        assert embedding_model_kwargs({"dtype": "float16"}) == {"model_kwargs": {"torch_dtype": expected}}

    def test_dtype_bfloat16(self):
        expected = self._expected_dtype("bfloat16")
        assert embedding_model_kwargs({"dtype": "bfloat16"}) == {"model_kwargs": {"torch_dtype": expected}}

    def test_dtype_float32(self):
        expected = self._expected_dtype("float32")
        assert embedding_model_kwargs({"dtype": "float32"}) == {"model_kwargs": {"torch_dtype": expected}}

    @staticmethod
    def _expected_dtype(name: str):
        """有 torch 时返回真实 enum（原语义），无 torch 时回退为字符串。"""
        try:
            import torch

            return getattr(torch, name)
        except ImportError:
            return f"torch.{name}"

    def test_dtype_skip_when_empty_string(self):
        assert embedding_model_kwargs({"dtype": ""}) == {}


# ---------------------------------------------------------------------------
# maybe_index_to_gpu
# ---------------------------------------------------------------------------


class TestMaybeIndexToGpu:
    def test_no_cuda_returns_cpu_index(self):
        mock_index = MagicMock()
        with patch.object(device_utils, "is_cuda_available", return_value=False):
            result_index, is_gpu = maybe_index_to_gpu(mock_index)
        assert result_index is mock_index
        assert is_gpu is False

    def test_faiss_available_index_to_gpu(self):
        mock_index = MagicMock()
        mock_gpu_index = MagicMock()
        mock_faiss = MagicMock()
        mock_faiss.index_cpu_to_gpu.return_value = mock_gpu_index

        with (
            patch.object(device_utils, "is_cuda_available", return_value=True),
            patch.dict("sys.modules", {"faiss": mock_faiss}),
        ):
            result_index, is_gpu = maybe_index_to_gpu(mock_index, gpu_id=0)

        assert result_index is mock_gpu_index
        assert is_gpu is True
        mock_faiss.index_cpu_to_gpu.assert_called_once()

    def test_faiss_exception_falls_back_to_cpu(self):
        mock_index = MagicMock()
        mock_faiss = MagicMock()
        mock_faiss.StandardGpuResources.side_effect = RuntimeError("libfaiss_gpu not found")

        with (
            patch.object(device_utils, "is_cuda_available", return_value=True),
            patch.dict("sys.modules", {"faiss": mock_faiss}),
        ):
            result_index, is_gpu = maybe_index_to_gpu(mock_index)

        # 失败时返回原始 index，is_gpu=False
        assert result_index is mock_index
        assert is_gpu is False


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
