"""
问答历史持久化存储

将每次问答对话保存为 JSON 文件，支持列表查看、加载继续追问、删除。

存储结构: data/chat_history/{session_id}.json
  {
    "session_id": "uuid",
    "title": "第一个问题截取",
    "created_at": "2026-08-04 12:00:00",
    "updated_at": "2026-08-04 12:05:00",
    "messages": [
      {"role": "user", "content": "...", "sources": [...], "ts": "..."},
      {"role": "assistant", "content": "...", "sources": [...], "ts": "..."},
      ...
    ]
  }

注意: session_id 以 "test_" 开头的不会持久化（排除单元测试产生的问答）。
"""
import os
import json
import time
import uuid
import logging
from typing import List, Optional

log = logging.getLogger("rag_demo.chat_history")


class ChatHistoryStore:
    """问答历史持久化"""

    def __init__(self, history_dir: str = ""):
        self._dir = history_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "chat_history"
        )
        os.makedirs(self._dir, exist_ok=True)

    def _is_test_session(self, session_id: str) -> bool:
        """判断是否为测试会话（不持久化）"""
        return session_id.startswith("test_")

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: list = None,
        title: str = "",
    ) -> dict:
        """
        追加一条消息到会话历史

        Args:
            session_id: 会话 ID
            role: "user" | "assistant"
            content: 消息内容
            sources: 来源列表（assistant 才有）
            title: 会话标题（通常取第一个问题）

        Returns:
            会话摘要 dict
        """
        if self._is_test_session(session_id):
            return {}

        filepath = os.path.join(self._dir, f"{session_id}.json")

        # 加载已有历史
        data = self._load_file(filepath)
        if data is None:
            data = {
                "session_id": session_id,
                "title": title or content[:30],
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "messages": [],
            }

        # 追加消息
        msg = {
            "role": role,
            "content": content,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if sources:
            msg["sources"] = sources
        data["messages"].append(msg)
        data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # 如果是第一条用户消息，更新标题
        if role == "user" and not data.get("title"):
            data["title"] = content[:30]

        self._save_file(filepath, data)
        return self._summarize(data)

    def list_sessions(self, limit: int = 50) -> List[dict]:
        """列出所有会话摘要"""
        sessions = []
        if not os.path.exists(self._dir):
            return sessions

        for fname in os.listdir(self._dir):
            if not fname.endswith(".json"):
                continue
            filepath = os.path.join(self._dir, fname)
            data = self._load_file(filepath)
            if data:
                sessions.append(self._summarize(data))

        # 按更新时间倒序
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return sessions[:limit]

    def get_session(self, session_id: str) -> Optional[dict]:
        """获取完整会话"""
        filepath = os.path.join(self._dir, f"{session_id}.json")
        return self._load_file(filepath)

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        filepath = os.path.join(self._dir, f"{session_id}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            return True
        return False

    def _summarize(self, data: dict) -> dict:
        """提取会话摘要（不含完整消息）"""
        return {
            "session_id": data.get("session_id", ""),
            "title": data.get("title", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "message_count": len(data.get("messages", [])),
        }

    @staticmethod
    def _load_file(filepath: str) -> Optional[dict]:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"加载历史文件失败: {filepath}: {e}")
            return None

    @staticmethod
    def _save_file(filepath: str, data: dict):
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"保存历史文件失败: {filepath}: {e}")
