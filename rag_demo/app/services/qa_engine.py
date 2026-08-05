"""
问答引擎 — 检索 + 冲突检测 + 生成 + 溯源

核心流程:
  1. 用户提问
  2. 语义检索 top-K 段落（DocStore.search）
  3. 冲突检测（检索结果中是否有矛盾）
  4. 拼接 context + 冲突提示 → LLM 生成
  5. 返回：答案 + 来源列表 + 冲突标记
"""
import logging
from typing import List, Optional
from dataclasses import dataclass, field

from app.services.doc_store import DocStore, RetrievedChunk
from app.services.chat import ChatSession
from app.services.config_store import ConfigStore
from app.services.chat_history import ChatHistoryStore

log = logging.getLogger("rag_demo.qa_engine")


@dataclass
class QAResponse:
    """问答结果"""
    answer: str
    sources: List[dict] = field(default_factory=list)   # [{text, source_file, location, score}]
    conflicts: List[dict] = field(default_factory=list)  # [{point, doc_a, doc_b}]
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

    def __init__(self, doc_store: DocStore, config_store: ConfigStore, history_store: ChatHistoryStore = None):
        self._doc_store = doc_store
        self._config = config_store
        self._sessions: dict = {}  # {session_id: ChatSession}
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

        # 6. 封装结果
        sources = [
            {
                "text": c.text[:200],
                "source_file": c.source_file,
                "location": c.location,
                "score": round(c.score, 3),
            }
            for c in chunks
        ]

        # 7. 持久化问答历史
        self._history.save_message(session_id, "user", question)
        self._history.save_message(session_id, "assistant", answer, sources=sources, title=question[:30])

        return QAResponse(
            answer=answer,
            sources=sources,
            conflicts=conflicts,
            has_conflicts=len(conflicts) > 0,
        )

    def reset_session(self, session_id: str = "default"):
        """重置指定会话"""
        if session_id in self._sessions:
            self._sessions[session_id].reset()

    def _get_session(self, session_id: str) -> ChatSession:
        """获取或创建会话"""
        if session_id not in self._sessions:
            system_prompt = self._config.get("prompts.system", "")
            llm_config = self._config.get_section("llm")
            max_history = self._config.get("chat.max_history", 20)
            self._sessions[session_id] = ChatSession(
                system_prompt=system_prompt,
                llm_config=llm_config,
                max_history=max_history,
                session_id=session_id,
            )
        return self._sessions[session_id]

    def _build_context(self, chunks: List[RetrievedChunk], conflicts: List[dict]) -> str:
        """构建发送给 LLM 的上下文文本"""
        template = self._config.get(
            "prompts.context_template",
            "[来源: {source} {location}]\n{text}\n"
        )

        parts = []
        for chunk in chunks:
            parts.append(template.format(
                source=chunk.source_file,
                location=chunk.location,
                text=chunk.text,
            ))

        context = "\n".join(parts)

        # 如果有冲突，追加醒目提示
        if conflicts:
            conflict_template = self._config.get("prompts.conflict_warning", "")
            conflict_desc = "\n".join([
                f"  - {c['point']}：{c['doc_a_file']}称「{c['doc_a_says']}」，"
                f"{c['doc_b_file']}称「{c['doc_b_says']}」"
                for c in conflicts
            ])
            if conflict_template:
                context += conflict_template.format(conflicts=conflict_desc)

        return context

    def _detect_conflicts(self, chunks: List[RetrievedChunk]) -> List[dict]:
        """
        检索检索结果中的矛盾

        策略：按 X 文档（被审核文档）的段落聚合，
        同一段落与多个其他文档的差异合并为一条矛盾记录。

        TODO: 接入 version_diff.check_conflicts() 做更精准的 LLM 判断
        """
        import hashlib

        # 1. 两两比较，收集所有原始冲突对
        raw_pairs = []
        for i, chunk_a in enumerate(chunks):
            for j, chunk_b in enumerate(chunks):
                if i >= j:
                    continue
                # 只比较来自不同文档的
                if chunk_a.source_file == chunk_b.source_file:
                    continue

                if chunk_a.score > 0.7 and chunk_b.score > 0.7:
                    sim = self._text_similarity(chunk_a.text, chunk_b.text)
                    if 0.5 < sim < 0.95:
                        raw_pairs.append({
                            "a": {"file": chunk_a.source_file, "loc": chunk_a.location, "text": chunk_a.text[:150], "score": round(chunk_a.score, 3)},
                            "b": {"file": chunk_b.source_file, "loc": chunk_b.location, "text": chunk_b.text[:150], "score": round(chunk_b.score, 3)},
                        })

        if not raw_pairs:
            return []

        # 2. 按 X 文档（固定侧）段落聚合
        #    - 以 X 文档名 + X 文本前50字 为 key 聚合
        #    - 每个 group 包含: point(主题), a(X侧), others(B1/B2/...各侧对比)
        groups = {}
        for pair in raw_pairs:
            sig = hashlib.md5(
                (pair["a"]["file"] + "|" + pair["a"]["text"][:50].strip()).encode()
            ).hexdigest()[:12]
            if sig not in groups:
                groups[sig] = {
                    "point": "可能存在描述差异",
                    "a": pair["a"],
                    "others": [],
                }
            groups[sig]["others"].append(pair["b"])

        # 3. 构建最终冲突列表（每条含一个 X + 多个 B 的对比）
        conflicts = []
        for g in groups.values():
            conflicts.append({
                "point": g["point"],
                "doc_a_file": g["a"]["file"],
                "doc_a_location": g["a"]["loc"],
                "doc_a_says": g["a"]["text"],
                "doc_others": [
                    {"file": b["file"], "location": b["loc"], "says": b["text"]}
                    for b in g["others"]
                ],
            })

        return conflicts

    @staticmethod
    def _text_similarity(text_a: str, text_b: str) -> float:
        """简单文本相似度（Jaccard on char-grams）"""
        if not text_a or not text_b:
            return 0.0
        # 2-gram
        grams_a = set(text_a[i:i+2] for i in range(len(text_a)-1))
        grams_b = set(text_b[i:i+2] for i in range(len(text_b)-1))
        if not grams_a or not grams_b:
            return 0.0
        return len(grams_a & grams_b) / len(grams_a | grams_b)
