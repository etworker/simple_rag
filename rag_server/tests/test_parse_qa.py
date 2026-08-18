"""LLM 解析质量 service 的结构化结果校验测试。"""

from unittest.mock import patch

from doc_parser.models import Document, Table

from app.services.parse_qa import review_document_parse_quality


def _document() -> Document:
    return Document(
        filename="sample.pdf",
        tables=[
            Table(
                headers=["字段", "值"],
                rows=[["字段", "值"], ["名称"]],
                page=4,
                index=1,
            )
        ],
    )


def _result(issue_id: str, *, decision: str = "pass", is_parse_error: bool = False) -> dict:
    return {
        "issue_id": issue_id,
        "decision": decision,
        "is_parse_error": is_parse_error,
        "confidence": 0.91,
        "reason": "结构可接受",
        "repair_backend": "manual",
        "page_range": [3, 5],
    }


def _issue_id() -> str:
    return "table_structure-1"


def test_complete_llm_result_is_applied():
    with patch("version_diff.llm_util.call_llm_json", return_value=[_result(_issue_id())]) as mocked:
        report = review_document_parse_quality(_document(), {}, {"max_retries": 0})

    issue = report.issues[0]
    assert mocked.call_count == 1
    assert report.incomplete is False
    assert report.status == "pass"
    assert report.stats["llm_checked"] == 1
    assert issue.llm_decision == "pass"
    assert issue.is_parse_error is False
    assert issue.suggested_backend == "manual"
    assert issue.suggested_page_range == [3, 5]


def test_missing_issue_marks_report_incomplete():
    with patch("version_diff.llm_util.call_llm_json", return_value=[]):
        report = review_document_parse_quality(_document(), {}, {"max_retries": 0})

    assert report.incomplete is True
    assert report.status == "review"
    assert report.issues[0].llm_decision == ""


def test_duplicate_issue_result_marks_report_incomplete():
    duplicate = [_result(_issue_id()), _result(_issue_id())]
    with patch("version_diff.llm_util.call_llm_json", return_value=duplicate):
        report = review_document_parse_quality(_document(), {}, {"max_retries": 0})

    assert report.incomplete is True
    assert report.status == "review"
    assert report.stats["llm_checked"] == 0


def test_llm_none_preserves_rule_report():
    with patch("version_diff.llm_util.call_llm_json", return_value=None):
        report = review_document_parse_quality(_document(), {}, {"max_retries": 0})

    assert report.incomplete is True
    assert report.status == "review"
    assert report.issues[0].kind == "table_structure"


def test_parse_error_is_saved_and_fails_report():
    result = _result(_issue_id(), decision="fail", is_parse_error=True)
    with patch("version_diff.llm_util.call_llm_json", return_value=[result]):
        report = review_document_parse_quality(_document(), {}, {"max_retries": 0})

    assert report.status == "fail"
    assert report.stats["llm_parse_errors"] == 1
    assert report.stats["llm_failures"] == 1
    assert report.issues[0].is_parse_error is True


if __name__ == "__main__":
    test_complete_llm_result_is_applied()
    test_missing_issue_marks_report_incomplete()
    test_duplicate_issue_result_marks_report_incomplete()
    test_llm_none_preserves_rule_report()
    test_parse_error_is_saved_and_fails_report()
    print("test_parse_qa: ok")
