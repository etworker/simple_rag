"""pytest 共享 fixtures：隔离 ConfigStore 保存路径，避免测试污染真实 rag_server/config.json。"""

import os
import shutil

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_config_save(tmp_path_factory):
    """所有测试的 ConfigStore 保存路径重定向到临时目录。

    历史问题：test_upload_persistence / test_e2e_web_flow 通过 patch ConfigStore.__init__
    把 cache.base_dir 指向临时目录，测试中 POST /api/config 触发 save() 时把临时路径
    写回了真实 rag_server/config.json（且 patch 泄漏影响后续测试）。
    本 fixture 在 session 级把 _config_path 重定向到临时文件，模块测试恢复时
    恢复为 _SESSION_PATCHED_INIT（而非原始 __init__），保证隔离全程有效。
    """
    from app.services import config_store as _cs

    _orig = _cs.ConfigStore.__init__
    tmp = str(tmp_path_factory.mktemp("config_save"))

    def _session_patched(self, config_path=None):
        _orig(self, config_path)
        self._config_path = os.path.join(tmp, "config.json")

    _cs.ConfigStore.__init__ = _session_patched
    _cs._SESSION_PATCHED_INIT = _session_patched
    yield
    _cs.ConfigStore.__init__ = _orig
    shutil.rmtree(tmp, ignore_errors=True)