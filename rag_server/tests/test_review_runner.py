"""review_runner 结果结构回归测试。"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app as _app  # noqa: F401
from app.services import review_runner


class _FakeEngine:
    def document_similarity(self, _new_filepath, _existing_filepath):
        return 0.42


class _FakeDiffEngine:
    def __init__(self, config=None):
        self.config = config

    def add(self, _filepath):
        return None

    def pre_review(self, *_args, **_kwargs):
        return SimpleNamespace(
            inconsistencies=[],
            suspects=[
                SimpleNamespace(
                    point="待人工复核的候选",
                    doc_a_file="new.pdf",
                    doc_a_location="第1页",
                    doc_a_says="要求 A",
                    doc_b_file="old.pdf",
                    doc_b_location="第2页",
                    doc_b_says="要求 B",
                    similarity=0.91,
                )
            ],
        )


@pytest.mark.parametrize("filename", ["new.pdf"])
def test_run_multi_compare_serializes_suspects(monkeypatch, filename):
    """跨文档比较应把 DiffResult.suspects 序列化到对应 group。"""
    import version_diff

    monkeypatch.setattr(version_diff, "DiffEngine", _FakeDiffEngine)
    monkeypatch.setattr(review_runner, "_build_engine_config", lambda: {})

    doc_meta = SimpleNamespace(
        doc_id="doc-b",
        filename="old.pdf",
        label="旧文档",
        file_hash="hash-b",
        filepath="old.pdf",
    )
    task = {
        "status": "running",
        "filename": filename,
        "file_hash": "hash-new",
        "result": {},
    }

    groups = review_runner._run_multi_compare(
        _FakeEngine(),
        "new.pdf",
        [doc_meta],
        task,
    )

    assert len(groups) == 1
    group = groups[0]
    assert group["compare_type"] == "conflict"
    assert group["inconsistencies"] == []
    assert len(group["suspects"]) == 1
    assert group["suspects"][0]["point"] == "待人工复核的候选"
    assert group["suspects"][0]["doc_a_id"] == "new.pdf#HASH-NEW"
    assert group["suspects"][0]["doc_b_id"] == "doc-b"
    assert "1 疑似" in task["result"].get("current_group", {}).get("message", "")


def test_persist_upload_content_uses_unique_temp_and_reuses_matching_file(monkeypatch, tmp_path):
    """重复保存同 hash 文件时不应覆盖固定 .tmp 或替换正在读取的目标。"""
    from app.main import app as _current_app  # noqa: F401
    from app.routes import review as review_routes
    from app.services.utils import compute_sha256, compute_sha256_bytes

    monkeypatch.setattr(review_routes._state.app, "upload_dir", str(tmp_path))
    content = b"stable upload content"
    file_hash = compute_sha256_bytes(content)

    first_path = review_routes._persist_upload_content(content, file_hash, "sample.PDF")
    second_path = review_routes._persist_upload_content(content, file_hash, "sample.pdf")

    assert first_path == second_path
    assert compute_sha256(first_path) == file_hash
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_upload_rejects_same_hash_review_task(monkeypatch):
    """已有未结束的同 hash 审核时，重复上传应返回明确的 409。"""
    from app.main import app as _current_app  # noqa: F401
    from io import BytesIO

    from fastapi import HTTPException, UploadFile
    from app.routes import _state
    from app.routes.review import upload_document
    from app.services.utils import compute_sha256_bytes

    content = b"already being reviewed"
    file_hash = compute_sha256_bytes(content)
    task_id = "duplicate-hash-review"
    _state.app.review_tasks[task_id] = {"status": "running", "file_hash": file_hash}
    try:
        upload = UploadFile(filename="sample.pdf", file=BytesIO(content))
        with pytest.raises(HTTPException) as error:
            await upload_document(upload, choice="", label="")
        assert error.value.status_code == 409
        assert task_id in str(error.value.detail)
    finally:
        _state.app.review_tasks.pop(task_id, None)
