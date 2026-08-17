"""
问答引擎 — 检索 + 冲突检测 + 生成 + 溯源

核心流程:
  1. 用户提问
  2. 语义检索 top-K 段落（DocStore.search）
  3. 冲突检测（检索结果中是否有矛盾）
  4. 拼接 context + 冲突提示 → LLM 生成
  5. 返回：答案 + 来源列表 + 冲突标记
"""

from collections import OrderedDict
from dataclasses import dataclass, field

from llm_chat import (
    ChatSession,
    Message,  # noqa: F401 - re-export for tests
)
from loguru import logger as log
from version_diff.conflict import detect_conflicts

from app.services.chat_history import ChatHistoryStore
from app.services.config_store import ConfigStore
from app.services.doc_store import DocStore, RetrievedChunk

# 会话上限：超过后淘汰最久未使用的会话，避免长运行服务内存无限增长
MAX_SESSIONS = 500


@dataclass
class QAResponse:
    """问答结果"""

    answer: str
    sources: list[dict] = field(default_factory=list)  # [{text, source_file, location, score}]
    conflicts: list[dict] = field(default_factory=list)  # [{point, doc_a, doc_b}]
    has_conflicts: bool = False

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "sources": self.sources,
            "conflicts": self.conflicts,
            "has_conflicts": self.has_conflicts,
        }


class QAEngine:
    """
    问答引擎

    Example:
        engine = QAEngine(doc_store, config_store)
        response = engine.ask("备份频率是多少？", session_id="user1")
        print(response.answer)
        print(response.sources)
        if response.has_conflicts:
            print("⚠️ 文档存在矛盾!")
    """

    def __init__(
        self,
        doc_store: DocStore,
        config_store: ConfigStore,
        history_store: ChatHistoryStore = None,
    ):
        self._doc_store = doc_store
        self._config = config_store
        # OrderedDict 保持插入顺序，便于 LRU 淘汰最旧会话
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()
        self._history = history_store or ChatHistoryStore()

    def ask(self, question: str, session_id: str = "default") -> QAResponse:
        """
        回答用户问题

        Args:
            question: 用户问题
            session_id: 会话 ID（支持多用户/多轮）

        Returns:
            QAResponse 包含答案、来源、冲突信息
        """
        # 1. 检索相关段落
        top_k = self._config.get("retrieval.top_k", 5)
        min_score = self._config.get("retrieval.min_followup_score", 0.45)
        chunks = self._doc_store.search(question, top_k=top_k)

        if not chunks:
            return QAResponse(
                answer="抱歉，未在文档库中找到与您问题相关的内容。请确认文档已入库，或尝试换个问法。",
                sources=[],
            )

        # 判断是否为追问（会话中已有历史）
        session = self._get_session(session_id)
        is_followup = len(session.messages) > 0

        # 追问时：如果检索结果质量太低，不注入 context，依赖对话历史
        if is_followup and chunks and chunks[0].score < min_score:
            log.info(f"追问检索质量低 (top={chunks[0].score:.3f} < {min_score})，使用对话历史")
            answer = session.ask(question, context="")
            return QAResponse(answer=answer, sources=[], conflicts=[], has_conflicts=False)

        # 2. 冲突检测（仅对高质量检索结果）
        conflicts = self._detect_conflicts(chunks)
        # 3. 构建上下文
        context = self._build_context(chunks, conflicts)

        # 5. LLM 生成答案
        answer = session.ask(question, context=context)

        # 6. 封装结果 —— sources 带编号
        # idx 是 chunk 序号（回答中的 [1][2] 引用编号）；
        # doc_id 是文档级标识（文件名#HASH8），前端用它映射到 B1/B2 文档编号；
        # label 是用户上传时填写的补充描述（tag，如版本号），legend 显示。
        _doc_meta_cache = {}

        def _get_label(doc_id: str) -> str:
            if doc_id not in _doc_meta_cache:
                meta = self._doc_store.get_document(doc_id)
                _doc_meta_cache[doc_id] = (meta.label if meta else "") or ""
            return _doc_meta_cache[doc_id]

        source_list = [
            {
                "idx": i,
                "text": c.text[:200],
                "source_file": c.source_file,
                "doc_id": c.source_file,
                "label": _get_label(c.source_file),
                "location": c.location,
                "score": round(c.score, 3),
            }
            for i, c in enumerate(chunks, start=1)
        ]

        # 7. 持久化问答历史
        self._history.save_message(session_id, "user", question)
        self._history.save_message(session_id, "assistant", answer, sources=source_list, title=question[:30])

        return QAResponse(
            answer=answer,
            sources=source_list,
            conflicts=conflicts,
            has_conflicts=len(conflicts) > 0,
        )

    def reset_session(self, session_id: str = "default"):
        """重置指定会话"""
        if session_id in self._sessions:
            self._sessions[session_id].reset()

    def _get_session(self, session_id: str) -> ChatSession:
        """获取或创建会话（超出上限时淘汰最久未使用的会话）"""
        if session_id not in self._sessions:
            if len(self._sessions) >= MAX_SESSIONS:
                # 淘汰最旧的会话（OrderedDict 首项是插入最早的）
                oldest_id, _ = self._sessions.popitem(last=False)
                log.info(f"♻️ 会话数达上限 {MAX_SESSIONS}，淘汰最旧会话: {oldest_id}")
            system_prompt = self._config.get("prompts.system", "")
            llm_config = self._config.get_llm_profile("qa")
            max_history = self._config.get("chat.max_history", 20)
            # 从 llm_config 构造 llm_chat.ChatSession 参数
            cfg = dict(llm_config)
            backend = cfg.pop("provider", "") or "bedrock"
            self._sessions[session_id] = ChatSession(
                system_prompt=system_prompt,
                backend=backend,
                max_history=max_history,
                **cfg,
            )
        else:
            # 标记为最近使用，维持 LRU 顺序
            self._sessions.move_to_end(session_id)
        return self._sessions[session_id]

    def _expand_chunk_context(self, chunk: RetrievedChunk, radius: int) -> str:
        """扩展命中 chunk 的上下文：取同文档前后 radius 个相邻段落拼接。

        段落切分（按语义/句号/章节）可能把一段完整内容拆成相邻几段，
        单段检索命中时 LLM 只能看到局部。此处把命中段及其前后 radius 段
        拼接成一个 context 块（用换行 + 来源标注区分），帮助 LLM 理解完整语境。
        """
        neighbors = self._doc_store.get_neighbor_texts(chunk.paragraph_index, radius)
        if not neighbors:
            return chunk.text
        blocks = []
        for n in neighbors:
            marker = "▼ 命中" if n.get("is_target") else ""
            blocks.append(f"[{n['location']}]{marker}\n{n['text']}")
        return "\n\n".join(blocks)

    def _build_context(self, chunks: list[RetrievedChunk], conflicts: list[dict]) -> str:
        """构建发送给 LLM 的上下文文本

        格式带编号 [1] [2] ...，便于 LLM 输出时用简短编号引用而非长文件名。
        """
        # 给每个 chunk 编号
        template = self._config.get("prompts.context_template", "[{idx}] {source} | {location}\n{text}\n")
        radius = int(self._config.get("retrieval.context_radius", 0) or 0)

        parts = []
        for i, chunk in enumerate(chunks, start=1):
            text = chunk.text
            if radius > 0:
                text = self._expand_chunk_context(chunk, radius) or text
            parts.append(
                template.format(
                    idx=i,
                    source=chunk.source_file,
                    location=chunk.location,
                    text=text,
                )
            )

        context = "\n".join(parts)

        # 编号引用指南
        ref_guide = (
            "## 引用指南\n"
            "回答中提及时请使用 [1]、[2] 这种简短编号指明出处（扩在括号内的数字），"
            "不要书写文件名或路径。示例：\n"
            "  ✅「备份频率为每天 [1]，保留 30 天 [3]」\n"
            "  ❌「备份频率为每天 [来源: xxx.pdf 第 6 页]」\n\n"
        )
        context = ref_guide + context

        # 如果有冲突，追加醒目提示
        if conflicts:
            conflict_template = self._config.get("prompts.conflict_warning", "")
            lines = []
            for c in conflicts:
                others_desc = "；".join(f"{o['file']}称「{o['says']}」" for o in c["doc_others"])
                lines.append(f"  - {c['point']}：{c['doc_a_file']}称「{c['doc_a_says']}」，{others_desc}")
            conflict_desc = "\n".join(lines)
            if conflict_template:
                context += conflict_template.format(conflicts=conflict_desc)

        return context

    def _detect_conflicts(self, chunks: list[RetrievedChunk]) -> list[dict]:
        """
        检索结果中的矛盾检测

        统一委托 ``version_diff.conflict.detect_conflicts``（单一实现）：
        1. score 预过滤 + Jaccard 2-gram 相似度门控
        2. LLM 确认（复用 ``judge_pairs`` 公共接口）
        3. LLM 不可用时回退到启发式聚合

        Returns:
            list[dict]，每项 {"point","doc_a_file","doc_a_location","doc_a_says","doc_others":[...]}
        """
        passages = [
            {
                "text": c.text,
                "source_file": c.source_file,
                "location": c.location,
                "score": c.score,
            }
            for c in chunks
        ]
        cd_config = self._config.get("conflict_detection", {})
        llm_config = self._config.get_llm_profile("conflict_detection")
        judge_config = self._config.get_section("judge")
        return detect_conflicts(
            passages,
            llm_config=llm_config,
            judge_config=judge_config,
            cd_config=cd_config,
        )
