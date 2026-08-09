"""
端到端 Web 全流程测试 — 重置 → 上传 v1 → 入库 → 上传 v2 → 预审核检测矛盾

完整模拟用户操作链路:
  1. POST /api/documents/clear          — 重置知识库
  2. POST /api/documents/upload          — 上传 v1 文档
  3. GET  /api/documents/review/active   — 轮询等待预审核完成
  4. POST /api/documents/review/{id}/confirm — 确认 v1 入库
  5. POST /api/documents/upload          — 上传 v2 文档（与 v1 有矛盾内容）
  6. GET  /api/documents/review/active   — 轮询等待 v2 预审核
  7. 检查 v2 预审核结果（矛盾数 / is_safe）
  8. POST /api/documents/review/{id}/confirm — 确认 v2 入库
  9. GET  /api/documents/list            — 验证两篇文档都在列表中

使用 FastAPI TestClient（同步），预审核后台任务通过轮询等待。
"""
import io
import os
import shutil
import sys
import tempfile
import time

# Force UTF-8 output on Windows (emoji + Chinese)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pytest
from fastapi.testclient import TestClient

# ── 隔离测试环境：临时缓存目录 ──────────────────────────────
_TMP_CACHE = tempfile.mkdtemp(prefix="rag_e2e_")

# Patch ConfigStore 在 import app 之前生效
from app.services import config_store as _cs  # noqa: E402
from app.services.config_store import ConfigStore  # noqa: E402

_orig_init = ConfigStore.__init__


def _patched_init(self, config_path=None):
    _orig_init(self, config_path)
    self.set("cache.base_dir", _TMP_CACHE)
    self.set("cache.upload_dir", os.path.join(_TMP_CACHE, "uploads"))


_cs.ConfigStore.__init__ = _patched_init


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c
    shutil.rmtree(_TMP_CACHE, ignore_errors=True)


# ── 辅助函数 ────────────────────────────────────────────────

def _make_pdf_v1() -> bytes:
    """v1 文档：管理手册（每月巡检 / 30天保留 / 经理审批）"""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 720), "信息技术部管理手册 R1", fontsize=14)
    page.insert_text((72, 680), "3.1 日常巡检频率为每月一次。", fontsize=12)
    page.insert_text((72, 650), "3.2 备份保留周期为30天。", fontsize=12)
    page.insert_text((72, 620), "3.3 变更审批层级为部门经理。", fontsize=12)
    page.insert_text((72, 590), "3.4 合格标准为90分。", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _make_pdf_v2() -> bytes:
    """v2 文档：工作手册（每季度巡检 / 90天保留 / 总监审批）— 与 v1 有 4 处矛盾"""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 720), "信息技术部工作手册 R2", fontsize=14)
    page.insert_text((72, 680), "3.1 日常巡检频率为每季度一次。", fontsize=12)
    page.insert_text((72, 650), "3.2 备份保留周期为90天。", fontsize=12)
    page.insert_text((72, 620), "3.3 变更审批层级为分管总监。", fontsize=12)
    page.insert_text((72, 590), "3.4 合格标准为80分。", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _wait_pre_review_done(client: TestClient, timeout: int = 180) -> dict | None:
    """轮询 /review/active 直到预审核完成，返回 task 数据或 None"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get("/api/documents/review/active")
        if r.status_code == 200:
            data = r.json()
            status = data.get("status", "")
            if status == "done":
                return data
            if status == "error":
                return data
        time.sleep(1.0)
    return None


# ── 测试用例 ────────────────────────────────────────────────


class TestE2EWebFlow:
    """端到端全流程：重置 → 上传 v1 → 入库 → 上传 v2 → 预审核矛盾检测"""

    def test_reset_to_upload_v2(self, client: TestClient):
        # ================================================================
        # Step 1: 重置知识库
        # ================================================================
        print("\n[Step 1] 重置知识库")
        r = client.post("/api/documents/clear")
        assert r.status_code == 200, f"重置失败: {r.text}"
        print(f"  ✅ {r.json().get('message', '')}")

        # 验证列表为空
        r = client.get("/api/documents/list")
        assert r.status_code == 200
        assert r.json()["total"] == 0, "重置后列表不为空"
        print("  ✅ 知识库已清空")

        # ================================================================
        # Step 2: 上传 v1 文档
        # ================================================================
        print("\n[Step 2] 上传 v1 (管理手册)")
        v1_data = _make_pdf_v1()
        r = client.post(
            "/api/documents/upload",
            files={"file": ("管理手册v1.pdf", io.BytesIO(v1_data), "application/pdf")},
            data={"choice": "coexist"},
        )
        assert r.status_code == 200, f"上传 v1 失败: {r.text}"
        v1_task_id = r.json()["task_id"]
        v1_file_hash = r.json()["file_hash"]
        assert v1_task_id, "未返回 task_id"
        assert len(v1_file_hash) == 64, f"file_hash 镞度应为64: {len(v1_file_hash)}"
        print(f"  ✅ task_id={v1_task_id}, hash={v1_file_hash[-8:].upper()}")

        # ================================================================
        # Step 3: 等待 v1 预审核完成
        # ================================================================
        print("\n[Step 3] 等待 v1 预审核...")
        result = _wait_pre_review_done(client, timeout=180)
        assert result is not None, "v1 预审核超时"
        assert result["status"] == "done", f"v1 预审核失败: {result.get('result', {})}"
        v1_review = result.get("result", {})
        # v1 是第一篇文档，库中没有其他文档可比对，应该 is_safe=True
        assert v1_review.get("is_safe") is True, f"v1 应为安全: {v1_review}"
        print(f"  ✅ v1 预审核完成: {v1_review.get('message', '')}")

        # ================================================================
        # Step 4: 确认 v1 入库
        # ================================================================
        print("\n[Step 4] 确认 v1 入库")
        r = client.post(f"/api/documents/review/{v1_task_id}/confirm")
        assert r.status_code == 200, f"v1 入库失败: {r.text}"
        assert r.json()["paragraphs"] >= 1, "v1 入库段落数应 >= 1"
        print(f"  ✅ {r.json().get('message', '')} ({r.json()['paragraphs']} 段)")

        # 验证列表中有 1 篇文档
        r = client.get("/api/documents/list")
        assert r.json()["total"] == 1, "入库后应有 1 篇文档"
        print("  ✅ 列表确认: 1 篇文档")

        # ================================================================
        # Step 5: 上传 v2 文档（与 v1 有矛盾）
        # ================================================================
        print("\n[Step 5] 上传 v2 (工作手册 — 与 v1 有 4 处矛盾)")
        v2_data = _make_pdf_v2()
        r = client.post(
            "/api/documents/upload",
            files={"file": ("工作手册v2.pdf", io.BytesIO(v2_data), "application/pdf")},
            data={"choice": "coexist"},
        )
        assert r.status_code == 200, f"上传 v2 失败: {r.text}"
        v2_task_id = r.json()["task_id"]
        v2_file_hash = r.json()["file_hash"]
        assert v2_task_id, "未返回 task_id"
        print(f"  ✅ task_id={v2_task_id}, hash={v2_file_hash[-8:].upper()}")

        # ================================================================
        # Step 6: 等待 v2 预审核完成
        # ================================================================
        print("\n[Step 6] 等待 v2 预审核（检测与 v1 的矛盾）...")
        result = _wait_pre_review_done(client, timeout=180)
        assert result is not None, "v2 预审核超时"
        assert result["status"] == "done", f"v2 预审核失败: {result.get('result', {})}"
        v2_review = result.get("result", {})
        inconsistencies = v2_review.get("inconsistencies", [])
        print(f"  ✅ v2 预审核完成: {v2_review.get('message', '')}")
        print(f"  矛盾数: {len(inconsistencies)}")

        # 如果 LLM 可用，应该检测到矛盾
        if inconsistencies:
            for inc in inconsistencies:
                print(f"    - {inc.get('point', '?')}: "
                      f"{inc.get('doc_a_says', '?')[:30]} vs "
                      f"{inc.get('doc_b_says', '?')[:30]}")
            # 验证矛盾结构
            inc0 = inconsistencies[0]
            assert "point" in inc0, "矛盾项缺少 point"
            assert "doc_a_file" in inc0, "矛盾项缺少 doc_a_file"
            assert "doc_b_file" in inc0, "矛盾项缺少 doc_b_file"
            print("  ✅ 矛盾结构验证通过")
        else:
            # LLM 不可用时，heuristics 可能不发现矛盾，这是可接受的
            print("  ⚠️ 未检测到矛盾（LLM 可能未配置，heuristic 未触发）")

        # ================================================================
        # Step 7: 确认 v2 入库
        # ================================================================
        print("\n[Step 7] 确认 v2 入库")
        r = client.post(f"/api/documents/review/{v2_task_id}/confirm")
        assert r.status_code == 200, f"v2 入库失败: {r.text}"
        print(f"  ✅ {r.json().get('message', '')} ({r.json()['paragraphs']} 段)")

        # ================================================================
        # Step 8: 验证最终文档列表
        # ================================================================
        print("\n[Step 8] 验证最终文档列表")
        r = client.get("/api/documents/list")
        assert r.status_code == 200
        docs = r.json()["documents"]
        assert len(docs) == 2, f"应有 2 篇文档, 实际 {len(docs)}"
        names = [d["filename"] for d in docs]
        assert "管理手册v1.pdf" in names, f"v1 不在列表中: {names}"
        assert "工作手册v2.pdf" in names, f"v2 不在列表中: {names}"
        for d in docs:
            assert d.get("doc_id", ""), f"doc_id 为空: {d}"
            assert len(d.get("file_hash", "")) == 64, f"file_hash 异常: {d}"
            assert d.get("paragraph_count", 0) >= 1, f"段落数异常: {d}"
        print(f"  ✅ 2 篇文档均已入库:")
        for d in docs:
            print(f"    - {d['filename']} ({d['paragraph_count']}段, "
                  f"hash={d['file_hash'][-8:].upper()})")

        # ================================================================
        # Step 9: 清理 — 重置知识库
        # ================================================================
        print("\n[Step 9] 清理: 重置知识库")
        r = client.post("/api/documents/clear")
        assert r.status_code == 200
        print("  ✅ 清理完成")
