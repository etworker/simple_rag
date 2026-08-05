"""
rag_demo 核心功能单元测试

测试层级:
  1. ConfigStore — 配置读写、点号访问、深度合并
  2. DocStore — 文档入库、检索、删除
  3. ChatSession — 多轮对话、历史管理
  4. QAEngine — 端到端问答（需要 LLM API）

用法:
    cd rag_demo 目录
    uv pip install -e . -e ../doc_parser -e ../version_diff
    uv run python tests/test_core.py
"""
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=" * 60)
print("rag_demo 核心功能测试")
print("=" * 60)


# ============================================================
# 测试 1: ConfigStore
# ============================================================
print("\n--- 测试1: ConfigStore ---")
from app.services.config_store import ConfigStore

config = ConfigStore()
assert config.get("llm.model") == "zai.glm-4.7-flash"
assert config.get("retrieval.top_k") == 5
assert config.get("nonexist.key", "default") == "default"
print(f"  get('llm.model') = {config.get('llm.model')}")

config.set("retrieval.top_k", 10)
assert config.get("retrieval.top_k") == 10
print(f"  set → get('retrieval.top_k') = {config.get('retrieval.top_k')}")

config.update({"llm": {"model": "moonshot.kimi-k2-thinking"}})
assert config.get("llm.model") == "moonshot.kimi-k2-thinking"
assert config.get("llm.region") == "us-east-1"  # 深度合并，不覆盖其他字段
print(f"  update → get('llm.model') = {config.get('llm.model')}")
print(f"  update → get('llm.region') = {config.get('llm.region')} (未被覆盖)")

d = config.to_dict()
assert "embedding" in d and "llm" in d and "retrieval" in d
print("  ✅ ConfigStore 全部通过")


# ============================================================
# 测试 2: DocStore
# ============================================================
print("\n--- 测试2: DocStore ---")
from app.services.doc_store import DocStore, RetrievedChunk

# 用默认配置初始化
store_config = ConfigStore().to_dict()
store = DocStore(store_config)

# 找测试文件
test_data = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "version_diff", "tests", "test_data"
)
test_files = [os.path.join(test_data, f) for f in sorted(os.listdir(test_data)) if f.endswith('.docx')]

if len(test_files) >= 2:
    # 入库两个文档
    meta1 = store.add_document(test_files[0])
    print(f"  入库: {meta1.filename} ({meta1.paragraph_count} 段)")
    meta2 = store.add_document(test_files[1])
    print(f"  入库: {meta2.filename} ({meta2.paragraph_count} 段)")

    assert store.total_documents == 2
    assert store.total_paragraphs > 0
    print(f"  总文档: {store.total_documents}, 总段落: {store.total_paragraphs}")

    # 检索测试
    results = store.search("备份频率")
    print(f"  搜索'备份频率': {len(results)} 条结果")
    if results:
        top = results[0]
        print(f"    top1: [{top.source_file}] score={top.score:.3f} '{top.text[:50]}...'")
        assert top.score > 0
        assert top.source_file != ""
    print("  ✅ DocStore 入库+检索通过")

    # 删除测试
    store.remove_document(meta1.filename)
    assert store.total_documents == 1
    print(f"  删除后: 总文档={store.total_documents}")
    print("  ✅ DocStore 删除通过")
else:
    print("  ⚠️ 未找到测试文件，跳过 DocStore 测试")


# ============================================================
# 测试 3: ChatSession
# ============================================================
print("\n--- 测试3: ChatSession ---")
from app.services.chat import ChatSession
from llm_chat import Message

session = ChatSession(
    system_prompt="你是测试助手",
    llm_config={"model": "test", "api_key_env": "AWS_BEARER_TOKEN_BEDROCK"},
    max_history=3,
)

# 测试历史管理（不调 LLM）
session.messages.append(Message(role="user", content="问题1"))
session.messages.append(Message(role="assistant", content="回答1"))
session.messages.append(Message(role="user", content="问题2"))
session.messages.append(Message(role="assistant", content="回答2"))
session.messages.append(Message(role="user", content="问题3"))
session.messages.append(Message(role="assistant", content="回答3"))
session.messages.append(Message(role="user", content="问题4"))
session.messages.append(Message(role="assistant", content="回答4"))

# max_history=3 → 最多保留 6 条消息
session._truncate()
assert len(session.messages) <= 6, f"截断失败: {len(session.messages)} > 6"
print(f"  历史截断: 8条 → {len(session.messages)}条 (max_history=3)")

# 测试 reset
session.reset()
assert len(session.messages) == 0
print("  reset: 清空成功")

# 测试实际 LLM 调用（需要 API key）
api_key = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
if api_key:
    session2 = ChatSession(
        system_prompt="你是测试助手，回答尽量简短。",
        llm_config={
            "model": "zai.glm-4.7-flash",
            "region": "us-east-1",
            "api_key_env": "AWS_BEARER_TOKEN_BEDROCK",
        },
    )
    answer = session2.ask("1+1等于几？")
    print(f"  LLM 多轮测试 Q1: '1+1等于几？' → '{answer[:50]}'")
    assert "2" in answer

    answer2 = session2.ask("那再加3呢？")
    print(f"  LLM 多轮测试 Q2: '那再加3呢？' → '{answer2[:50]}'")
    assert "5" in answer2
    print("  ✅ ChatSession 多轮对话通过")
else:
    print("  ⚠️ 未设置 AWS_BEARER_TOKEN_BEDROCK，跳过 LLM 调用测试")

print("  ✅ ChatSession 基础测试通过")


# ============================================================
# 测试 4: QAEngine 端到端
# ============================================================
print("\n--- 测试4: QAEngine ---")
from app.services.qa_engine import QAEngine, QAResponse

if api_key and len(test_files) >= 2:
    # 重新建 store
    config_store = ConfigStore()
    store2 = DocStore(config_store.to_dict())
    store2.add_document(test_files[0])
    store2.add_document(test_files[1])

    qa = QAEngine(store2, config_store)
    response = qa.ask("备份文件的保留周期是多少？")

    print(f"  问题: '备份文件的保留周期是多少？'")
    print(f"  答案: '{response.answer[:100]}...'")
    print(f"  来源: {len(response.sources)} 条")
    print(f"  冲突: {response.has_conflicts}")
    if response.sources:
        print(f"    来源1: [{response.sources[0]['source_file']}] {response.sources[0]['location']}")
    assert response.answer != ""
    assert len(response.sources) > 0
    print("  ✅ QAEngine 端到端通过")
else:
    print("  ⚠️ 缺少 API Key 或测试文件，跳过端到端测试")


print("\n" + "=" * 60)
print("rag_demo 核心功能测试完成")
print("=" * 60)
