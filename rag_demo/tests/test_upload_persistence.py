"""
集成测试 — 验证上传文件在"服务重启"后仍可访问（Bug #5 回归测试）。

场景：
1. 上传文档 → 确认入库
2. 模拟"服务重启"：重新初始化 DocStore + routes._state.init()
3. 验证：
   a) documents/list 返回的文档数 ≥ 确认时新加入的文档
   b) documents/pdf 路由能返回该文件的 200 响应
   c) review/{id}/progress 再次触发预审核时不会 FileNotFoundError
"""

import io
import os
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient

# 临时目录隔离测试用全局缓存根
_TMP_CACHE = tempfile.mkstemp(prefix="rag_test_")[1]
os.unlink(_TMP_CACHE)
os.makedirs(_TMP_CACHE)
os.environ["CACHE_DIR"] = _TMP_CACHE


@pytest.fixture(scope="module")
def client():
    """构建测试客户端（使用真实文件系统，仅做 restart 模拟）。"""
    # 在 import app 之前覆写环境变量
    os.environ.setdefault("CACHE_DIR", _TMP_CACHE)

    # Patch ConfigStore 中的 base_dir 为临时目录，避免污染真实数据
    from app.services import config_store as _cs
    from app.services.config_store import ConfigStore

    _orig_init = ConfigStore.__init__

    def _patched_init(self, config_path=None):
        _orig_init(self, config_path)
        # 替换 base_dir
        self.set("cache.base_dir", _TMP_CACHE)
        self.set("cache.upload_dir", os.path.join(_TMP_CACHE, "uploads"))

    _cs.ConfigStore.__init__ = _patched_init

    from app.main import app

    with TestClient(app) as c:
        yield c

    # 测试后清理临时目录
    shutil.rmtree(_TMP_CACHE, ignore_errors=True)


def _make_small_pdf(name: str = "test_regression.pdf") -> bytes:
    """构造一个最小可解析 PDF（PyMuPDF 生成）。"""
    try:
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 700), "测试文档内容，足够让 pdfplumber 识别", fontsize=14)
        page.insert_text(
            (72, 660), "总则部分：本文档包含通用技术要求。", fontsize=12
        )
        data = doc.tobytes()
        doc.close()
        return data
    except ImportError:
        # Fallback: 无效 PDF 会导致预审核失败，不能测试"持久化成功"
        raise


def test_upload_confirm_survives_restart(client: TestClient):
    """Bug #5 回归测试：确认入库的文件在模拟服务重启后仍可访问。"""

    # === 阶段 1: 上传并确认 ===
    pdf_data = _make_small_pdf()
    resp = client.post(
        "/api/documents/upload",
        files={"file": ("manual.pdf", io.BytesIO(pdf_data), "application/pdf")},
        data={"choice": "coexist"},
    )
    assert resp.status_code == 200, f"上传失败: {resp.text}"
    task_id = resp.json()["task_id"]

    # 等待预审核完成（轮询 /progress SSE 的替代方案：轮询 active 状态）
    import time

    deadline = time.time() + 120
    done = False
    while time.time() < deadline:
        r = client.get("/api/documents/review/active")
        if r.status_code == 200 and r.json().get("status") == "done":
            done = True
            break
        time.sleep(0.5)
    assert done, "预审核未在规定时间内完成"

    # 确认入库
    r = client.post(f"/api/documents/review/{task_id}/confirm")
    assert r.status_code == 200, f"确认失败: {r.text}"
    filename = r.json()["filename"]
    paragraphs = r.json()["paragraphs"]
    assert paragraphs >= 1

    # === 阶段 2: 模拟服务重启 ===
    from app.routes import _state, review, documents

    # 备份当前的 doc_store（模拟进程退出前内存状态）
    old_doc_count = len(_state.app.doc_store._documents)

    # 重新加载路由状态（相当于 FastAPI 启动时的 init() 流程）
    _state.app._load_review_cache()  # 复位内存任务
    _state.app.confirmed_or_rejected.clear()
    # doc_store 本身保留（磁盘持久化模拟：_documents 从磁盘重新加载）
    _state.app.doc_store._load_from_disk()

    # === 阶段 3: 验证持久性 ===
    # (a) 文档列表仍包含该文档
    r = client.get("/api/documents/list")
    assert r.status_code == 200
    docs = r.json()["documents"]
    active_docs = [d for d in docs if d["status"] == "active"]
    assert any(
        d["filename"] == "manual.pdf" for d in active_docs
    ), f"重启后找不到新文档。active={[d['filename'] for d in active_docs]}"

    # (b) PDF 文件实际可访问（FileNotFound 不会再发生）
    r = client.get(f"/api/documents/pdf?name=manual.pdf")
    assert r.status_code == 200, f"PDF 下载失败: {r.text}"
    assert len(r.content) > 0, "PDF 文件为空"

    # (c) 新上传另一个文档，预审核流程仍能加载已有文档
    pdf2 = _make_small_pdf("test2.pdf")
    r = client.post(
        "/api/documents/upload",
        files={"file": ("another.pdf", io.BytesIO(pdf2), "application/pdf")},
        data={"choice": "coexist"},
    )
    # 如果 bug 仍在，这里会 FileNotFoundError → 500
    assert r.status_code == 200, f"第二次上传失败: {r.text}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
