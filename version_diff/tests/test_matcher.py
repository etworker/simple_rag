"""正文段落确定性配对回归测试。"""

from types import SimpleNamespace

import numpy as np

from version_diff.matcher import pair_paragraphs


class _FakeVectorStore:
    def get_or_compute(self, _filepath, paragraphs, _model):
        return np.ones((len(paragraphs), 2), dtype="float32"), None


def test_pairs_renumbered_content_embedded_in_merged_paragraph():
    old = [
        SimpleNamespace(
            text="1.1.16 完成上级领导交办的其他工作。",
            chapter="1.1",
        )
    ]
    new = [
        SimpleNamespace(
            text="1.1.14 加强公司内部IT 从业人员的培养与梯队建设； 1.1.15 完成上级领导交办的其他工作。",
            chapter="1.1",
        )
    ]

    pairs = pair_paragraphs(
        old,
        new,
        model=None,
        vector_store=_FakeVectorStore(),
        file_a="old.pdf",
        file_b="new.pdf",
    )

    assert len(pairs) == 1
    assert pairs[0][:2] == (0, 0)
    assert pairs[0][2] == 0.95
