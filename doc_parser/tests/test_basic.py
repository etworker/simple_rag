"""
doc_parser 包测试脚本

用法:
    cd doc_parser 目录
    uv pip install -e .
    python tests/test_basic.py
"""

import os
import sys

# 测试 1: 导入
print("=" * 50)
print("测试 doc_parser 包")
print("=" * 50)

try:
    from doc_parser import Document, Paragraph, Table, parse

    print("\n✅ 测试1: 导入成功")
    print(f"   parse = {parse}")
    print(f"   Document = {Document}")
    print(f"   Paragraph = {Paragraph}")
    print(f"   Table = {Table}")
except ImportError as e:
    print(f"\n❌ 测试1: 导入失败 — {e}")
    sys.exit(1)

# 测试 2: 模型创建
print("\n--- 测试2: 模型实例化 ---")
p = Paragraph(text="这是一段测试文字", page=5, chapter="2.1", chapter_title="适用范围")
print(f"   Paragraph.text = '{p.text}'")
print(f"   Paragraph.location = '{p.location}'")
assert p.location == "第5页 / §2.1 / 适用范围", f"location 不对: {p.location}"
print("   ✅ Paragraph 模型正确")

t = Table(headers=["项目", "要求"], rows=[["备份频率", "每日"]], page=10)
print(f"   Table.location = '{t.location}'")
assert "第10页" in t.location
print("   ✅ Table 模型正确")

d = Document(filename="test.pdf", paragraphs=[p], tables=[t])
assert d.filename == "test.pdf"
assert len(d.paragraphs) == 1
assert len(d.tables) == 1
print("   ✅ Document 模型正确")

# 测试 3: 解析真实文件（如果有测试数据）
print("\n--- 测试3: 解析真实文件 ---")
test_files = [
    r"C:\Users\0937\Documents\work\simple_rag\version_diff\tests\test_data\A_IT管理规定.docx",
    r"C:\Users\0937\Documents\work\rag\versionrag\data\test_cross_doc_consistency\A_IT管理规定.docx",
]

parsed = False
for test_file in test_files:
    if os.path.exists(test_file):
        try:
            doc = parse(test_file)
            print(f"   解析: {doc.filename}")
            print(f"   段落数: {len(doc.paragraphs)}")
            print(f"   表格数: {len(doc.tables)}")
            if doc.paragraphs:
                print(f"   首段: '{doc.paragraphs[0].text[:60]}...'")
            assert len(doc.paragraphs) > 0, "段落数为0"
            print("   ✅ Word 解析成功")
            parsed = True
            break
        except NotImplementedError:
            print("   ⚠️ 解析功能未实现（NotImplementedError）")
            break
        except Exception as e:
            print(f"   ❌ 解析失败: {e}")
            break

if not parsed:
    # 尝试 PDF
    pdf_files = [
        r"C:\Users\0937\Documents\work\rag\versionrag\data\real_aviation_manuals\(二级)(司批)信息技术管理手册.pdf",
    ]
    for test_file in pdf_files:
        if os.path.exists(test_file):
            try:
                doc = parse(test_file)
                print(f"   解析: {doc.filename}")
                print(f"   段落数: {len(doc.paragraphs)}")
                print(f"   表格数: {len(doc.tables)}")
                assert len(doc.paragraphs) > 0
                print("   ✅ PDF 解析成功")
                parsed = True
            except NotImplementedError:
                print("   ⚠️ 解析功能未实现（NotImplementedError）")
            except Exception as e:
                print(f"   ❌ 解析失败: {e}")
            break

if not parsed:
    print("   ⚠️ 未找到测试文件或解析未实现")

print("\n" + "=" * 50)
print("doc_parser 基础测试完成")
print("=" * 50)
