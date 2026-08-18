"""解析结果 Markdown 和确定性 QA 规则测试。"""

from doc_parser.models import Document, Paragraph, Table
from doc_parser.qa import document_to_markdown, inspect_document


def _document() -> Document:
    return Document(
        filename="sample.pdf",
        paragraphs=[
            Paragraph(text="这是跨页段落，", page=2, page_end=3, index=1, order=1),
            Paragraph(text="重复内容", page=3, index=1, order=2),
            Paragraph(text="重复内容", page=3, index=1, order=3),
            Paragraph(text="重复内容", page=4, index=1, order=4),
            Paragraph(text="重复内容", page=4, index=1, order=5),
        ],
        tables=[
            Table(
                headers=["字段", "值"],
                rows=[["字段", "值"], ["名称"]],
                page=5,
                index=1,
                order=1,
            ),
            Table(
                headers=["序号", "说明"],
                rows=[["序号", "说明"], ["1", "跨页数据"]],
                page=6,
                page_end=7,
                index=2,
                order=2,
            ),
        ],
    )


def test_markdown_keeps_page_and_table_metadata():
    markdown = document_to_markdown(_document())

    assert "<!-- parse_qa filename='sample.pdf' -->" in markdown
    assert "type='paragraph'" in markdown
    assert "page=2" in markdown and "page_end=3" in markdown
    assert "type='table'" in markdown
    assert "columns=2" in markdown and "rows=2" in markdown
    assert "跨页数据" in markdown


def test_rules_find_cross_page_and_bad_table():
    document = _document()
    before = document.to_dict()
    report = inspect_document(document)

    kinds = {issue.kind for issue in report.issues}
    assert "cross_page_paragraph" in kinds
    assert "table_structure" in kinds
    assert "cross_page_table" in kinds
    assert report.stats["cross_page_paragraphs"] == 1
    assert report.stats["cross_page_tables"] == 1
    assert report.status == "fail"
    assert len({issue.issue_id for issue in report.issues}) == len(report.issues)
    assert document.to_dict() == before


def test_duplicate_issue_ids_are_disambiguated():
    document = Document(
        filename="duplicates.pdf",
        paragraphs=[
            Paragraph(text="相同的长段落内容用于重复检测以及页眉页脚异常验证", page=1, index=1),
            Paragraph(text="相同的长段落内容用于重复检测以及页眉页脚异常验证", page=1, index=1),
            Paragraph(text="另一组长段落内容用于重复检测以及页眉页脚异常验证", page=2, index=1),
            Paragraph(text="另一组长段落内容用于重复检测以及页眉页脚异常验证", page=2, index=1),
        ],
    )
    report = inspect_document(document)
    ids = [issue.issue_id for issue in report.issues if issue.kind == "duplicate_paragraph"]

    assert ids == ["duplicate_paragraph-1", "duplicate_paragraph-1-2"]
    assert len(ids) == len(set(ids))


if __name__ == "__main__":
    test_markdown_keeps_page_and_table_metadata()
    test_rules_find_cross_page_and_bad_table()
    test_duplicate_issue_ids_are_disambiguated()
    print("test_qa: ok")
