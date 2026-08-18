"""同文档多版本管理单元测试。"""

import json

import numpy as np

from app.services.doc_store import DocMeta, DocStore
from doc_parser import Paragraph


def _bare_store() -> DocStore:
    """构造不加载 embedding 模型的最小 DocStore。"""
    store = DocStore.__new__(DocStore)
    store._documents = {}
    store._paragraphs = []
    store._config = {"retrieval": {"top_k": 5, "similarity_threshold": 0.3}}
    return store


def _meta(
    doc_id: str,
    filename: str,
    *,
    status: str,
    added_at: str,
    family_id: str,
    is_primary: bool,
) -> DocMeta:
    return DocMeta(
        filename=filename,
        filepath=f"{doc_id}.pdf",
        doc_id=doc_id,
        added_at=added_at,
        status=status,
        family_id=family_id,
        is_primary=is_primary,
    )


def test_family_id_normalizes_filename_case_and_whitespace():
    assert DocStore.family_id_for_filename("  Manual  .PDF") == DocStore.family_id_for_filename("manual .pdf")
    assert DocStore.family_id_for_filename("手册.pdf") != DocStore.family_id_for_filename("另一份手册.pdf")


def test_latest_document_uses_added_at_not_insertion_order():
    store = _bare_store()
    family_id = DocStore.family_id_for_filename("manual.pdf")
    older = _meta(
        "manual.pdf#OLD",
        "manual.pdf",
        status="active",
        added_at="2026-08-01 10:00:00",
        family_id=family_id,
        is_primary=False,
    )
    newer = _meta(
        "manual.pdf#NEW",
        "manual.pdf",
        status="active",
        added_at="2026-08-02 10:00:00",
        family_id=family_id,
        is_primary=True,
    )
    # 故意以“新、旧”顺序插入，验证不依赖 dict 插入顺序。
    store._documents = {newer.doc_id: newer, older.doc_id: older}

    assert store.get_latest_document_by_filename("manual.pdf").doc_id == newer.doc_id


def test_set_primary_deactivates_previous_version_atomically():
    store = _bare_store()
    store._save_to_disk = lambda: None
    family_id = DocStore.family_id_for_filename("manual.pdf")
    old = _meta(
        "manual.pdf#OLD",
        "manual.pdf",
        status="active",
        added_at="2026-08-01 10:00:00",
        family_id=family_id,
        is_primary=True,
    )
    new = _meta(
        "manual.pdf#NEW",
        "manual.pdf",
        status="inactive",
        added_at="2026-08-02 10:00:00",
        family_id=family_id,
        is_primary=False,
    )
    store._documents = {old.doc_id: old, new.doc_id: new}

    result = store.set_primary_document(new.doc_id)

    assert result is new
    assert old.status == "inactive"
    assert old.is_primary is False
    assert new.status == "active"
    assert new.is_primary is True
    assert store.list_documents() == [new]
    assert store.list_all_documents() == [old, new]


def test_search_filters_inactive_vectors_before_top_k():
    class FakeModel:
        def encode(self, _queries, normalize_embeddings=True):
            assert normalize_embeddings is True
            return np.array([[1.0, 0.0]], dtype=np.float32)

    class FakeRetriever:
        count = 3

        def search(self, _query_embedding, _top_k):
            # inactive 文档排在第 1 名；过滤后仍应补足两个 active 结果。
            return (
                np.array([0.99, 0.98, 0.97], dtype=np.float32),
                np.array([0, 1, 2], dtype=np.int64),
            )

    store = _bare_store()
    family_id = DocStore.family_id_for_filename("manual.pdf")
    active = _meta(
        "manual.pdf#ACTIVE",
        "manual.pdf",
        status="active",
        added_at="2026-08-02 10:00:00",
        family_id=family_id,
        is_primary=True,
    )
    inactive = _meta(
        "manual.pdf#INACTIVE",
        "manual.pdf",
        status="inactive",
        added_at="2026-08-01 10:00:00",
        family_id=family_id,
        is_primary=False,
    )
    store._documents = {active.doc_id: active, inactive.doc_id: inactive}
    store._paragraphs = [
        Paragraph(text="当前规则 1", source_file=active.doc_id, page=1),
        Paragraph(text="历史规则", source_file=inactive.doc_id, page=1),
        Paragraph(text="当前规则 2", source_file=active.doc_id, page=2),
    ]
    store._retriever = FakeRetriever()
    store._get_model = lambda: FakeModel()

    results = store.search("当前规则", top_k=2)

    assert [result.source_file for result in results] == [active.doc_id, active.doc_id]
    assert all(store.get_document(result.source_file).status == "active" for result in results)


def test_load_migrates_legacy_same_filename_versions_to_one_primary(tmp_path):
    persist_dir = tmp_path / "doc_store"
    persist_dir.mkdir()
    filename = "manual.pdf"
    old_id = f"{filename}#OLD"
    new_id = f"{filename}#NEW"
    legacy = {
        old_id: {
            "filename": filename,
            "filepath": str(tmp_path / "old.pdf"),
            "doc_id": old_id,
            "added_at": "2026-08-01 10:00:00",
            "status": "active",
            "file_hash": "old-hash",
            "label": "R1",
        },
        new_id: {
            "filename": filename,
            "filepath": str(tmp_path / "new.pdf"),
            "doc_id": new_id,
            "added_at": "2026-08-02 10:00:00",
            "status": "active",
            "file_hash": "new-hash",
            "label": "R2",
        },
    }
    (persist_dir / "documents.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    (persist_dir / "paragraphs.json").write_text("[]", encoding="utf-8")

    store = DocStore(
        {
            "persist_dir": str(persist_dir),
            "parse_cache_dir": str(tmp_path / "parse_cache"),
            "vector_cache_dir": str(tmp_path / "vector_cache"),
            "cache": {},
            "embedding": {},
        }
    )

    assert store._documents[old_id].status == "inactive"
    assert store._documents[old_id].is_primary is False
    assert store._documents[new_id].status == "active"
    assert store._documents[new_id].is_primary is True
    assert store._documents[old_id].family_id == store._documents[new_id].family_id
    assert [doc.doc_id for doc in store.list_documents()] == [new_id]
