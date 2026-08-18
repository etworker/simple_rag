"""VectorStore embedding progress tests."""

from types import SimpleNamespace

import numpy as np

from version_diff.vectorstore import VectorStore


class _FakeModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        assert normalize_embeddings is True
        assert show_progress_bar is False
        self.calls.append(len(texts))
        return np.ones((len(texts), 3), dtype=np.float32)


def _paragraphs(count):
    return [SimpleNamespace(text=f"段落 {i}", chapter="", chapter_title="") for i in range(count)]


def test_embedding_progress_reports_batches_and_preserves_result(tmp_path):
    store = VectorStore(cache_dir=str(tmp_path / "vectors"), config_hash="progress-test")
    model = _FakeModel()
    events = []
    paragraphs = _paragraphs(5)

    embeddings, index = store.get_or_compute(
        "document.pdf",
        paragraphs,
        model,
        on_progress=events.append,
        batch_size=2,
    )

    assert model.calls == [2, 2, 1]
    assert embeddings.shape == (5, 3)
    assert index.ntotal == 5
    assert events[0]["status"] == "running"
    assert events[-1]["status"] == "done"
    assert events[-1]["completed"] == 5
    assert events[-1]["total"] == 5
    assert events[-1]["batch_index"] == 3
    assert events[-1]["batch_total"] == 3
    assert [event["completed"] for event in events] == [0, 2, 4, 5]

    cached_events = []
    cached_embeddings, cached_index = store.get_or_compute(
        "document.pdf",
        paragraphs,
        model,
        on_progress=cached_events.append,
        batch_size=2,
    )

    assert model.calls == [2, 2, 1]
    assert np.array_equal(cached_embeddings, embeddings)
    assert cached_index.ntotal == 5
    assert len(cached_events) == 1
    assert cached_events[0]["status"] == "cached"
    assert cached_events[0]["cached"] is True
    assert cached_events[0]["completed"] == 5


def test_embedding_progress_reports_empty_input(tmp_path):
    store = VectorStore(cache_dir=str(tmp_path / "vectors"), config_hash="empty-test")
    events = []

    embeddings, index = store.get_or_compute(
        "empty.pdf",
        [],
        _FakeModel(),
        on_progress=events.append,
    )

    assert embeddings.shape == (0, 0)
    assert index is None
    assert events == [
        {
            "phase": "embedding",
            "status": "empty",
            "completed": 0,
            "total": 0,
            "batch_index": 0,
            "batch_total": 0,
            "pct": 0,
            "cached": False,
        }
    ]
