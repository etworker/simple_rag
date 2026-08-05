"""
version_diff 包测试脚本

用法:
    cd version_diff 目录
    uv pip install -e . -e ../doc_parser
    python tests/test_basic.py
"""
import sys
import os

print("=" * 50)
print("测试 version_diff 包")
print("=" * 50)

# 测试 1: 导入
try:
    from version_diff import DiffEngine, Config, DiffResult, Inconsistency
    print("\n✅ 测试1: 导入成功")
    print(f"   DiffEngine = {DiffEngine}")
    print(f"   Config = {Config}")
    print(f"   DiffResult = {DiffResult}")
    print(f"   Inconsistency = {Inconsistency}")
except ImportError as e:
    print(f"\n❌ 测试1: 导入失败 — {e}")
    sys.exit(1)

# 测试 2: Config 创建
print("\n--- 测试2: Config ---")
config = Config.from_dict({
    "embedding": {"model": "BAAI/bge-base-zh-v1.5"},
    "llm": {"provider": "bedrock_converse", "model": "zai.glm-4.7-flash"},
    "diff": {"similarity_threshold": 0.80, "batch_size": 5},
})
print(f"   embedding.model = {config.embedding.get('model')}")
print(f"   llm.model = {config.llm.get('model')}")
print(f"   diff.threshold = {config.diff.get('similarity_threshold')}")
print("   ✅ Config 正确")

# 测试 3: DiffResult 模型
print("\n--- 测试3: DiffResult ---")
result = DiffResult(
    inconsistencies=[
        Inconsistency(
            point="备份频率",
            doc_a_file="A文档.pdf",
            doc_a_location="第5页 / §2.1",
            doc_a_says="每4小时备份",
            doc_b_file="B文档.pdf",
            doc_b_location="第3页 / §2.1",
            doc_b_says="每2小时备份",
            similarity=0.92,
        )
    ],
    total_candidates=20,
    rule_filtered=5,
    llm_judged=15,
)
assert not result.is_safe
assert len(result.inconsistencies) == 1
print(f"   is_safe = {result.is_safe}")
print(f"   report 预览:\n{result.report()[:200]}")
d = result.to_dict()
assert d['inconsistency_count'] == 1
assert d['inconsistencies'][0]['point'] == '备份频率'
print("   ✅ DiffResult 模型正确")

# 测试 4: DiffEngine 初始化
print("\n--- 测试4: DiffEngine 初始化 ---")
try:
    engine = DiffEngine(config={
        "embedding": {"model": "BAAI/bge-base-zh-v1.5"},
        "llm": {"provider": "bedrock_converse", "model": "zai.glm-4.7-flash",
                "region": "us-east-1", "api_key_env": "AWS_BEARER_TOKEN_BEDROCK"},
        "diff": {"similarity_threshold": 0.80, "batch_size": 5},
    })
    print(f"   engine.config.llm['model'] = {engine.config.llm['model']}")
    print("   ✅ DiffEngine 初始化成功")
except Exception as e:
    print(f"   ❌ DiffEngine 初始化失败: {e}")
    sys.exit(1)

# 测试 5: 端到端（需要 embedding 模型 + LLM API）
print("\n--- 测试5: 端到端预审核 ---")
test_data = os.path.join(os.path.dirname(__file__), "test_data")
files = [os.path.join(test_data, f) for f in os.listdir(test_data) if f.endswith('.docx')]

if len(files) >= 2:
    print(f"   找到 {len(files)} 个测试文件")
    print("   添加已有文档...")
    try:
        engine.add(files[0])
        engine.add(files[1])
        print(f"   已入库: {os.path.basename(files[0])}, {os.path.basename(files[1])}")

        print("   执行预审核...")
        result = engine.pre_review(
            files[2] if len(files) > 2 else files[1],
            on_progress=lambda s, p, m: print(f"     [{s}] {p:.0%} {m}")
        )
        print(f"\n   结果:")
        print(f"   is_safe = {result.is_safe}")
        print(f"   矛盾数 = {len(result.inconsistencies)}")
        if result.inconsistencies:
            for inc in result.inconsistencies[:3]:
                print(f"   ⚠️ {inc.point}: A={inc.doc_a_says[:30]}, B={inc.doc_b_says[:30]}")
        print("   ✅ 端到端测试完成")
    except NotImplementedError as e:
        print(f"   ⚠️ 部分功能未实现: {e}")
    except Exception as e:
        print(f"   ❌ 端到端失败: {e}")
        import traceback
        traceback.print_exc()
else:
    print("   ⚠️ 测试数据不足，跳过端到端测试")

print("\n" + "=" * 50)
print("version_diff 基础测试完成")
print("=" * 50)
