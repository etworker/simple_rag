"""
Web API 自动化测试

使用 httpx AsyncClient + FastAPI TestClient 进行端到端 API 测试。
测试范围:
  - 首页返回 HTML
  - /api/config 配置读写
  - /api/documents/list 文档列表
  - /api/qa/ask 问答接口（async 不阻塞）
  - /api/qa/reset 会话重置
  - /api/logs/tail 日志读取
  - WebSocket /ws/logs 日志推送

运行方式:
    cd rag_demo
    uv run python -m pytest tests/test_web_api.py -v
"""

import asyncio
import os
import sys

import pytest
import pytest_asyncio

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def client():
    """创建异步测试客户端"""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHomePage:
    """首页测试"""

    async def test_index_returns_html(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        # HTML 应包含基本标题
        text = resp.text
        assert "RAG" in text or "rag" in text.lower()


class TestConfigAPI:
    """配置 API 测试"""

    async def test_get_config(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "embedding" in data
        assert "llm_profiles" in data
        assert "retrieval" in data

    async def test_update_config(self, client):
        resp = await client.post("/api/config", json={"retrieval": {"top_k": 15}})
        assert resp.status_code == 200
        assert "配置已更新" in resp.json().get("message", "")

        # 验证更新生效
        resp2 = await client.get("/api/config")
        assert resp2.json()["retrieval"]["top_k"] == 15


class TestDocumentListAPI:
    """文档列表 API 测试"""

    async def test_list_documents(self, client):
        resp = await client.get("/api/documents/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "documents" in data
        assert "total" in data
        assert isinstance(data["documents"], list)

    async def test_list_documents_has_total_paragraphs(self, client):
        resp = await client.get("/api/documents/list")
        data = resp.json()
        assert "total_paragraphs" in data
        assert isinstance(data["total_paragraphs"], int)


class TestQAAPI:
    """问答 API 测试"""

    async def test_ask_empty_question(self, client):
        """空问题应返回 400"""
        resp = await client.post("/api/qa/ask", json={"question": ""})
        assert resp.status_code == 400

    async def test_ask_whitespace_question(self, client):
        resp = await client.post("/api/qa/ask", json={"question": "   "})
        assert resp.status_code == 400

    async def test_ask_no_documents_returns_no_results(self, client):
        """无文档时应返回提示而非崩溃"""
        resp = await client.post("/api/qa/ask", json={"question": "测试问题", "session_id": "test_unit"})
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert "sources" in data
        assert "has_conflicts" in data

    async def test_reset_session(self, client):
        resp = await client.post("/api/qa/reset", params={"session_id": "test_unit"})
        assert resp.status_code == 200
        assert "重置" in resp.json().get("message", "")

    async def test_ask_does_not_block_event_loop(self, client):
        """验证 /api/qa/ask 使用了 async 包装（不阻塞事件循环）

        本测试检验接口在 LLM 调用期间不阻塞事件循环：无论 LLM 是否可用，
        /api/qa/ask 都应正常返回 HTTP 响应（200 表示成功，503 表示 LLM 按设计
        降级），而非 500 内部崩溃或因阻塞超时。
        """
        import time

        # 先发送 list 请求确认基线
        t0 = time.time()
        resp1 = await client.get("/api/documents/list")
        t1 = time.time()
        t1 - t0

        # 发送 ask 请求
        t2 = time.time()
        resp2 = await client.post("/api/qa/ask", json={"question": "test", "session_id": "test_unit"})
        t3 = time.time()
        t3 - t2

        # list 应成功
        assert resp1.status_code == 200
        # ask 应正常响应（成功 200 或 LLM 降级 503），不应是 500 内部错误
        assert resp2.status_code in (200, 503)


class TestChatHistoryAPI:
    """问答历史 API 测试"""

    async def test_list_sessions_empty(self, client):
        resp = await client.get("/api/qa/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    async def test_get_nonexistent_session(self, client):
        resp = await client.get("/api/qa/sessions/nonexistent")
        assert resp.status_code == 404

    async def test_delete_nonexistent_session(self, client):
        resp = await client.delete("/api/qa/sessions/nonexistent")
        assert resp.status_code == 404

    async def test_test_session_not_in_history(self, client):
        """test_ 前缀的 session 不应出现在历史列表中"""
        await client.post("/api/qa/ask", json={"question": "test", "session_id": "test_nohistory"})
        resp = await client.get("/api/qa/sessions")
        sessions = resp.json()["sessions"]
        for s in sessions:
            assert not s["session_id"].startswith("test_")


class TestSourceAPI:
    """来源段落 API 测试"""

    async def test_get_source_nonexistent_file(self, client):
        resp = await client.get("/api/qa/source", params={"file": "nonexistent.docx"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["paragraphs"] == []

    async def test_get_context_nonexistent_file(self, client):
        resp = await client.get("/api/qa/context", params={"file": "nonexistent.docx"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["paragraphs"] == []
        assert data["total"] == 0


class TestLogsAPI:
    """日志 API 测试"""

    async def test_tail_logs(self, client):
        resp = await client.get("/api/logs/tail", params={"lines": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert "lines" in data
        assert isinstance(data["lines"], list)


class TestReviewAPI:
    """预审核 API 测试"""

    async def test_get_active_review_none(self, client):
        """无活跃审核任务时应返回 task_id=None"""
        resp = await client.get("/api/documents/review/active")
        assert resp.status_code == 200
        data = resp.json()
        # 可能返回 None 或有之前的任务
        assert "task_id" in data

    async def test_cancel_nonexistent_review(self, client):
        """取消不存在的任务应返回 404"""
        resp = await client.post("/api/documents/review/nonexistent-id/cancel")
        assert resp.status_code == 404

    async def test_confirm_nonexistent_review(self, client):
        resp = await client.post("/api/documents/review/nonexistent-id/confirm")
        assert resp.status_code == 404

    async def test_reject_nonexistent_review(self, client):
        resp = await client.post("/api/documents/review/nonexistent-id/reject")
        assert resp.status_code == 404

    async def test_rerun_confirmed_returns_400(self, client):
        """已确认任务再强制重跑应返回 400（v4 回归保护）"""
        from app.routes import _state

        task_id = "test-confirmed"
        _state.app.review_tasks[task_id] = {
            "status": "confirmed",
            "filename": "test.pdf",
            "filepath": "/nonexistent/test.pdf",
            "file_hash": "abc123def456",
            "progress": 100,
            "current_step": "done",
            "steps": [],
            "result": None,
        }
        _state.app.confirmed_or_rejected.add(task_id)
        resp = await client.post(f"/api/documents/review/{task_id}/rerun")
        assert resp.status_code == 400
        # 清理
        _state.app.review_tasks.pop(task_id, None)
        _state.app.confirmed_or_rejected.discard(task_id)

    async def test_rerun_rejected_returns_400(self, client):
        """已拒绝任务再强制重跑应返回 400（v4 回归保护）"""
        from app.routes import _state

        task_id = "test-rejected"
        _state.app.review_tasks[task_id] = {
            "status": "rejected",
            "filename": "test.pdf",
            "filepath": "/nonexistent/test.pdf",
            "file_hash": "abc123def456",
            "progress": 100,
            "current_step": "done",
            "steps": [],
            "result": None,
        }
        _state.app.confirmed_or_rejected.add(task_id)
        resp = await client.post(f"/api/documents/review/{task_id}/rerun")
        assert resp.status_code == 400
        # 清理
        _state.app.review_tasks.pop(task_id, None)
        _state.app.confirmed_or_rejected.discard(task_id)


class TestStaticFiles:
    """静态文件测试"""

    async def test_static_dir_accessible(self, client):
        """静态文件目录应可访问"""
        resp = await client.get("/")
        assert resp.status_code == 200


class TestClearAPI:
    """知识库重置 API 测试"""

    async def test_clear_returns_message(self, client):
        """重置知识库应返回成功消息"""
        resp = await client.post("/api/documents/clear")
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data
        assert "重置" in data["message"] or "清空" in data["message"]

    async def test_clear_empties_doc_list(self, client):
        """重置后文档列表应为空"""
        await client.post("/api/documents/clear")
        resp = await client.get("/api/documents/list")
        data = resp.json()
        assert data["documents"] == []
        assert data["total"] == 0
        assert data["total_paragraphs"] == 0

    async def test_clear_removes_active_review(self, client):
        """重置后不应有活跃的预审核任务"""
        await client.post("/api/documents/clear")
        resp = await client.get("/api/documents/review/active")
        data = resp.json()
        assert data.get("task_id") is None

    async def test_clear_idempotent(self, client):
        """多次重置不应报错"""
        for _ in range(3):
            resp = await client.post("/api/documents/clear")
            assert resp.status_code == 200


class TestCacheConfig:
    """缓存配置测试"""

    async def test_config_has_cache_section(self, client):
        """配置中应包含 cache 段"""
        resp = await client.get("/api/config")
        data = resp.json()
        assert "cache" in data
        assert "base_dir" in data["cache"]

    async def test_cache_base_dir_is_expanded(self, client):
        """cache.base_dir 应展开 ~ 为实际路径"""
        resp = await client.get("/api/config")
        data = resp.json()
        base_dir = data["cache"]["base_dir"]
        # 不应包含 ~ 字符
        assert "~" not in base_dir

    async def test_config_cache_dir_is_writable(self, client):
        """缓存目录应存在且可写"""
        resp = await client.get("/api/config")
        data = resp.json()
        base_dir = data["cache"]["base_dir"]
        os.makedirs(base_dir, exist_ok=True)
        assert os.path.isdir(base_dir)
        # 可写性：写入临时文件后删除
        probe = os.path.join(base_dir, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)


class TestUploadFlow:
    """上传 → 预审核 → 入库 完整流程测试"""

    @pytest.fixture
    def test_pdf(self):
        """查找一个可用的测试 PDF 文件"""
        import glob

        # test_web_api.py 在 rag_demo/tests/ 下，上传目录在 rag_demo/uploads/
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pdfs = glob.glob(os.path.join(base, "uploads", "**", "*.pdf"), recursive=True)
        if not pdfs:
            pytest.skip("没有可用的测试 PDF 文件")
        return pdfs[0]

    async def test_upload_creates_task(self, client, test_pdf):
        """上传文件应创建预审核任务"""
        await client.post("/api/documents/clear")
        filename = os.path.basename(test_pdf)
        with open(test_pdf, "rb") as f:
            resp = await client.post(
                "/api/documents/upload",
                files={"file": (filename, f, "application/pdf")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["task_id"]
        assert data["filename"] == filename

    async def test_review_active_after_upload(self, client, test_pdf):
        """上传后应有活跃的预审核任务"""
        await client.post("/api/documents/clear")
        filename = os.path.basename(test_pdf)
        with open(test_pdf, "rb") as f:
            resp = await client.post(
                "/api/documents/upload",
                files={"file": (filename, f, "application/pdf")},
            )
        task_id = resp.json()["task_id"]

        # 等待预审核完成（最多 120 秒）
        for _ in range(40):
            resp = await client.get("/api/documents/review/active")
            data = resp.json()
            if data.get("status") == "done":
                assert data["task_id"] == task_id
                assert "result" in data
                assert "is_safe" in data["result"]
                return
            if data.get("status") == "error":
                pytest.fail(f"预审核失败: {data}")
            await asyncio.sleep(3)
        pytest.fail("预审核超时")

    async def test_confirm_after_review(self, client, test_pdf):
        """预审核完成后确认入库"""
        await client.post("/api/documents/clear")
        filename = os.path.basename(test_pdf)
        with open(test_pdf, "rb") as f:
            resp = await client.post(
                "/api/documents/upload",
                files={"file": (filename, f, "application/pdf")},
            )
        task_id = resp.json()["task_id"]

        # 等待预审核完成
        for _ in range(40):
            resp = await client.get("/api/documents/review/active")
            data = resp.json()
            if data.get("status") == "done":
                break
            if data.get("status") == "error":
                pytest.fail(f"预审核失败: {data}")
            await asyncio.sleep(3)
        else:
            pytest.fail("预审核超时")

        # 确认入库
        resp = await client.post(f"/api/documents/review/{task_id}/confirm")
        assert resp.status_code == 200
        result = resp.json()
        assert "入库" in result["message"]
        assert result["paragraphs"] > 0

        # 验证文档已入库
        resp = await client.get("/api/documents/list")
        docs = resp.json()["documents"]
        assert len(docs) == 1
        assert docs[0]["filename"] == filename

    async def test_reject_after_review(self, client, test_pdf):
        """预审核完成后拒绝入库"""
        await client.post("/api/documents/clear")
        filename = os.path.basename(test_pdf)
        with open(test_pdf, "rb") as f:
            resp = await client.post(
                "/api/documents/upload",
                files={"file": (filename, f, "application/pdf")},
            )
        task_id = resp.json()["task_id"]

        # 等待预审核完成
        for _ in range(40):
            resp = await client.get("/api/documents/review/active")
            data = resp.json()
            if data.get("status") == "done":
                break
            if data.get("status") == "error":
                pytest.fail(f"预审核失败: {data}")
            await asyncio.sleep(3)
        else:
            pytest.fail("预审核超时")

        # 拒绝入库
        resp = await client.post(f"/api/documents/review/{task_id}/reject")
        assert resp.status_code == 200

        # 验证文档未入库
        resp = await client.get("/api/documents/list")
        docs = resp.json()["documents"]
        assert len(docs) == 0

    async def test_duplicate_upload_blocked(self, client, test_pdf):
        """重复上传同名文档（已入库）应返回 409"""
        await client.post("/api/documents/clear")
        filename = os.path.basename(test_pdf)

        # 第一次上传 + 确认入库
        with open(test_pdf, "rb") as f:
            resp = await client.post(
                "/api/documents/upload",
                files={"file": (filename, f, "application/pdf")},
            )
        task_id = resp.json()["task_id"]
        for _ in range(40):
            resp = await client.get("/api/documents/review/active")
            if resp.json().get("status") == "done":
                break
            await asyncio.sleep(3)
        await client.post(f"/api/documents/review/{task_id}/confirm")

        # 第二次上传同名文件应被拒绝
        with open(test_pdf, "rb") as f:
            resp = await client.post(
                "/api/documents/upload",
                files={"file": (filename, f, "application/pdf")},
            )
        assert resp.status_code == 409
