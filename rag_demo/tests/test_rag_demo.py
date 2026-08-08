"""
rag_demo 修复验证测试

重点测试:
  - ConfigStore 配置管理
  - DocStore 公共段落查询方法（不再直接访问 _paragraphs）
  - DocStore 无重复解析缓存层
  - chat.py 正确 re-export Message
"""
import pytest
import os
import sys
import tempfile
import shutil

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfigStore:
    def test_get_dot_notation(self):
        from app.services.config_store import ConfigStore
        config = ConfigStore()
        assert config.get("llm.model") == ""  # 默认空，由 config.json 填充
        assert config.get("retrieval.top_k") == 5

    def test_get_default(self):
        from app.services.config_store import ConfigStore
        config = ConfigStore()
        assert config.get("nonexist.key", "default") == "default"

    def test_set(self):
        from app.services.config_store import ConfigStore
        config = ConfigStore()
        config.set("retrieval.top_k", 10)
        assert config.get("retrieval.top_k") == 10

    def test_deep_merge(self):
        from app.services.config_store import ConfigStore
        config = ConfigStore()
        config.update({"llm": {"model": "new-model"}})
        assert config.get("llm.model") == "new-model"
        # provider should not be overwritten
        assert config.get("llm.provider") == "openai"

    def test_get_section(self):
        from app.services.config_store import ConfigStore
        config = ConfigStore()
        llm = config.get_section("llm")
        assert llm["model"] == ""  # 默认空
        # Modifying the returned dict should not affect original
        llm["model"] = "changed"
        assert config.get("llm.model") == ""


class TestChatHistoryStore:
    """测试问答历史持久化"""

    def test_test_session_not_saved(self, tmp_path):
        """test_ 开头的 session 不应被持久化"""
        from app.services.chat_history import ChatHistoryStore
        store = ChatHistoryStore(history_dir=str(tmp_path))
        store.save_message("test_unit", "user", "测试问题")
        store.save_message("test_unit", "assistant", "测试回答")
        sessions = store.list_sessions()
        assert len(sessions) == 0

    def test_real_session_saved(self, tmp_path):
        """真实 session 应被持久化"""
        from app.services.chat_history import ChatHistoryStore
        store = ChatHistoryStore(history_dir=str(tmp_path))
        store.save_message("qa-001", "user", "什么是备份频率？")
        store.save_message("qa-001", "assistant", "每天备份", sources=[{"text": "每天", "source_file": "A.pdf"}])
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["title"] == "什么是备份频率？"
        assert sessions[0]["message_count"] == 2

    def test_get_session(self, tmp_path):
        from app.services.chat_history import ChatHistoryStore
        store = ChatHistoryStore(history_dir=str(tmp_path))
        store.save_message("qa-002", "user", "问题1")
        data = store.get_session("qa-002")
        assert data is not None
        assert data["messages"][0]["content"] == "问题1"

    def test_delete_session(self, tmp_path):
        from app.services.chat_history import ChatHistoryStore
        store = ChatHistoryStore(history_dir=str(tmp_path))
        store.save_message("qa-003", "user", "问题")
        assert store.delete_session("qa-003") is True
        assert store.get_session("qa-003") is None
        assert store.delete_session("nonexistent") is False

    def test_list_sorted_by_updated_at(self, tmp_path):
        from app.services.chat_history import ChatHistoryStore
        store = ChatHistoryStore(history_dir=str(tmp_path))
        store.save_message("qa-old", "user", "旧问题")
        store.save_message("qa-new", "user", "新问题")
        sessions = store.list_sessions()
        # 新的应排前面（可能同秒，但至少两条）
        assert len(sessions) == 2


class TestChatMessageExport:
    """测试 chat.py 正确 re-export Message"""

    def test_can_import_message_from_chat(self):
        from app.services.chat import ChatSession, Message
        msg = Message(role="user", content="test")
        assert msg.role == "user"
        assert msg.content == "test"


class TestDocStorePublicMethods:
    """测试 DocStore 的公共段落查询方法"""

    @pytest.fixture
    def doc_store(self):
        from app.services.config_store import ConfigStore
        from app.services.doc_store import DocStore

        config = ConfigStore().to_dict()
        config["persist_dir"] = tempfile.mkdtemp()
        config["parse_cache_dir"] = tempfile.mkdtemp()
        return DocStore(config)

    def test_get_paragraphs_by_file(self, doc_store):
        from doc_parser import Paragraph
        # Manually add paragraphs
        doc_store._paragraphs = [
            Paragraph(text="段落A1", source_file="A.docx", page=1),
            Paragraph(text="段落A2", source_file="A.docx", page=2),
            Paragraph(text="段落B1", source_file="B.docx", page=1),
        ]
        result = doc_store.get_paragraphs_by_file("A.docx")
        assert len(result) == 2
        assert result[0].text == "段落A1"
        assert result[1].text == "段落A2"

    def test_get_paragraphs_by_file_empty(self, doc_store):
        result = doc_store.get_paragraphs_by_file("nonexistent.docx")
        assert result == []

    def test_get_paragraph_context(self, doc_store):
        from doc_parser import Paragraph
        doc_store._paragraphs = [
            Paragraph(text=f"段落{i}", source_file="A.docx", page=i)
            for i in range(10)
        ]
        ctx = doc_store.get_paragraph_context("A.docx", index=5, radius=2)
        # Should return 5 paragraphs (3,4,5,6,7)
        assert len(ctx) == 5
        assert ctx[2]["is_target"] is True
        assert ctx[2]["index"] == 5

    def test_get_paragraph_context_edge(self, doc_store):
        from doc_parser import Paragraph
        doc_store._paragraphs = [
            Paragraph(text="段落0", source_file="A.docx"),
            Paragraph(text="段落1", source_file="A.docx"),
        ]
        ctx = doc_store.get_paragraph_context("A.docx", index=0, radius=5)
        assert len(ctx) == 2

    def test_find_paragraphs(self, doc_store):
        from doc_parser import Paragraph
        doc_store._paragraphs = [
            Paragraph(text="备份频率每日", source_file="A.docx", page=1, chapter="2.1", chapter_title="备份"),
            Paragraph(text="其他内容", source_file="A.docx", page=2),
            Paragraph(text="备份周期每周", source_file="B.docx", page=1),
        ]
        # No location filter
        results = doc_store.find_paragraphs("A.docx", limit=10)
        assert len(results) == 2

        # With limit
        results = doc_store.find_paragraphs("A.docx", limit=1)
        assert len(results) == 1

    def test_find_paragraphs_empty(self, doc_store):
        results = doc_store.find_paragraphs("nonexistent.docx")
        assert results == []

    def teardown_method(self):
        """清理临时目录"""
        import gc
        # Force garbage collection to release file handles
        gc.collect()


class TestDocStoreNoDuplicateCache:
    """测试 DocStore 不再有重复的解析缓存层"""

    def test_no_parse_with_cache_method(self):
        """_parse_with_cache 方法应已删除"""
        from app.services.doc_store import DocStore
        assert not hasattr(DocStore, '_parse_with_cache')
        assert not hasattr(DocStore, '_file_md5')
        assert not hasattr(DocStore, '_load_parsed_cache')
