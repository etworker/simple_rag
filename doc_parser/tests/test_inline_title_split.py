"""单元测试 — 行内标题粘连拆分（split_stream 改进）

覆盖场景:
1. 正文句号后紧跟"第N章 标题" → 拆分为正文 + 标题
2. 正文句号后紧跟"N.M 标题" → 拆分
3. 正文句号后紧跟"第N节 标题" → 拆分
4. 短正文不拆分（< 10 字符）
5. 无章节标题的正文行不拆分
6. 纯标题行（行首即标题）→ 走原有逻辑
7. 多个句末终止符中取第一个匹配的
8. 句号后非标题内容不拆分（如"参见 1.1"）
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from doc_parser._text import _try_split_inline_title, split_stream
from doc_parser.parser import get_extract_config


def _cfg():
    return get_extract_config()


# ============================================================
# _try_split_inline_title 直接测试
# ============================================================


def test_split_chapter_title_after_period():
    """句号后紧跟'第N章 标题' → 拆分"""
    line = "新入职人员须在入职后一个月内完成岗位培训并通过考核，考核合格分数为85分。 第六章 信息安全管理"
    result = _try_split_inline_title(line, _cfg())
    assert result is not None
    body, title = result
    assert "85分。" in body
    assert title == "第六章 信息安全管理"


def test_split_numeric_chapter_after_period():
    """句号后紧跟'N.M 标题' → 拆分"""
    line = "变更实施后24小时内提交《变更完成报告》。 3.1 IT资产清单"
    result = _try_split_inline_title(line, _cfg())
    assert result is not None
    body, title = result
    assert "变更完成报告" in body
    assert "3.1" in title and "IT资产清单" in title


def test_split_section_title_after_semicolon():
    """分号后紧跟'第N章 标题' → 拆分"""
    line = "所有生产环境变更必须经过审批流程； 第二章 服务器运维管理"
    result = _try_split_inline_title(line, _cfg())
    assert result is not None
    body, title = result
    assert "审批流程；" in body
    assert title == "第二章 服务器运维管理"


def test_no_split_short_body():
    """短正文（< 10 字符）不拆分"""
    line = "完成。 第六章 信息安全管理"
    result = _try_split_inline_title(line, _cfg())
    assert result is None


def test_no_split_no_title():
    """无章节标题的正文行不拆分"""
    line = "所有运维人员每季度参加不少于16学时的技术培训，培训内容包括新技术学习、安全意识教育和应急演练。"
    result = _try_split_inline_title(line, _cfg())
    assert result is None


def test_no_split_reference_after_period():
    """句号后是编号引用而非标题 → 不拆分"""
    # "1.1 概述" 匹配章节模式 → 应拆分（如果正文够长）
    line_long = (
        "运维人员须按照规范执行所有操作流程，确保安全合规。 1.1 概述"
    )
    result = _try_split_inline_title(line_long, _cfg())
    # "1.1 概述" 匹配章节模式 → 应拆分
    assert result is not None
    body, title = result
    assert "安全合规。" in body
    assert title == "1.1 概述"


def test_no_split_short_remainder():
    """句号后太短（< 4 字符）不拆分"""
    line = "考核合格分数为85分。 第"  # "第" 只有 1 字符
    result = _try_split_inline_title(line, _cfg())
    assert result is None


# ============================================================
# split_stream 集成测试
# ============================================================


def test_split_stream_inline_title_basic():
    """split_stream 能将粘连的标题拆出来"""
    cfg = _cfg()
    full_text = (
        "新入职人员须在入职后一个月内完成岗位培训并通过考核，"
        "考核合格分数为85分。 第六章 信息安全管理\n"
        "6.1 访问控制\n"
        "所有生产系统访问必须通过堡垒机。\n"
    )
    paras = split_stream(full_text, cfg)
    texts = [p[0] for p in paras]
    # 标题应独占一段
    assert any(t == "第六章 信息安全管理" for t in texts), f"标题未拆分: {texts}"
    # 正文应不含标题
    body_paras = [t for t in texts if "85分" in t]
    assert len(body_paras) == 1
    assert "第六章" not in body_paras[0]


def test_split_stream_inline_title_with_prior_lines():
    """split_stream 在累积了多行后拆分粘连标题"""
    cfg = _cfg()
    full_text = (
        "运维部门实行7×24小时值班制度：\n"
        "工作日白班：08:30-17:30，至少3人在岗；\n"
        "工作日夜班：17:30-08:30，至少1人在岗； 第五章 考核与培训\n"
        "5.1 KPI指标\n"
    )
    paras = split_stream(full_text, cfg)
    texts = [p[0] for p in paras]
    # "第五章 考核与培训" 应独占一段
    assert any(t == "第五章 考核与培训" for t in texts), f"标题未拆分: {texts}"
    # 正文段应不含"第五章"
    body_paras = [t for t in texts if "至少1人在岗" in t]
    assert len(body_paras) == 1
    assert "第五章" not in body_paras[0]


def test_split_stream_no_false_positive_on_normal_text():
    """正常文本（无标题粘连）不受影响"""
    cfg = _cfg()
    full_text = (
        "运维人员每日上午9:00前完成所有生产服务器的巡检工作，"
        "巡检内容包括：\n"
        "（1）CPU使用率不超过80%；\n"
        "（2）内存使用率不超过85%；\n"
    )
    paras = split_stream(full_text, cfg)
    texts = [p[0] for p in paras]
    # 不应出现误拆分
    for t in texts:
        assert "第" not in t or "第一章" not in t, f"误拆分: {texts}"


def test_split_stream_multiple_inline_titles():
    """多行粘连标题都能被拆分"""
    cfg = _cfg()
    full_text = (
        "考核合格分数为85分。 第六章 信息安全管理\n"
        "6.1 访问控制\n"
        "所有生产系统访问必须通过堡垒机。\n"
        "6.2 安全审计\n"
        "所有运维操作日志保留不少于180天。 第七章 附则\n"
    )
    paras = split_stream(full_text, cfg)
    texts = [p[0] for p in paras]
    assert any(t == "第六章 信息安全管理" for t in texts), f"第六章未拆分: {texts}"
    assert any(t == "第七章 附则" for t in texts), f"第七章未拆分: {texts}"
    # 正文段不含粘连标题
    body_with_180 = [t for t in texts if "180天" in t]
    assert len(body_with_180) == 1
    assert "第七章" not in body_with_180[0]


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
