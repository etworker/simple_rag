"""
问答路由 — 多轮对话 + 溯源 + 冲突标记
"""

import asyncio
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routes import _state
from app.routes._state import init_qa  # noqa: F401 - re-export for main.py

router = APIRouter()


class AskRequest(BaseModel):
    question: str
    session_id: str = "default"


@router.post("/ask")
async def ask(req: AskRequest):
    """
    问答接口

    返回:
      - answer: LLM 生成的答案
      - sources: 来源段落列表
      - conflicts: 冲突列表（如有）
      - has_conflicts: 是否存在冲突
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")

    qa_engine = _state.app.qa_engine
    try:
        response = await asyncio.to_thread(qa_engine.ask, req.question, session_id=req.session_id)
    except RuntimeError as e:
        # API Key 未配置等运行时错误
        if "API Key" in str(e) or "未配置" in str(e):
            raise HTTPException(status_code=503, detail=f"LLM 服务不可用: {e}") from e
        raise HTTPException(status_code=500, detail=f"问答引擎错误: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"内部错误: {e}") from e
    return response.to_dict()


@router.post("/reset")
async def reset_session(session_id: str = "default"):
    """重置对话历史"""
    _state.app.qa_engine.reset_session(session_id)
    return {"message": "对话已重置"}


@router.get("/sessions")
async def list_sessions(limit: int = 50):
    """列出所有问答历史会话"""
    sessions = _state.app.qa_engine._history.list_sessions(limit=limit)
    return {"sessions": sessions, "total": len(sessions)}


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """获取指定会话的完整问答历史"""
    data = _state.app.qa_engine._history.get_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="会话不存在")
    return data


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除指定问答会话"""
    success = _state.app.qa_engine._history.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "会话已删除"}


@router.get("/source")
async def get_source_paragraph(file: str, location: str = ""):
    """
    获取指定文档指定位置的段落原文

    用于前端点击引用时展示原文内容
    """
    file = unquote(file)
    location = unquote(location)
    doc_store = _state.app.qa_engine._doc_store
    matches = doc_store.find_paragraphs(file, location=location, limit=5)
    return {"file": file, "location": location, "paragraphs": matches}


@router.get("/context")
async def get_context_paragraphs(file: str, index: int = 0, radius: int = 3):
    """
    获取指定文档中某段落及其前后上下文

    用于矛盾对比时展示原文上下文
    """
    file = unquote(file)
    doc_store = _state.app.qa_engine._doc_store
    paras = doc_store.get_paragraph_context(file, index=index, radius=radius)
    doc_paras = doc_store.get_paragraphs_by_file(file)
    target = min(index, len(doc_paras) - 1) if doc_paras else 0
    return {
        "file": file,
        "paragraphs": paras,
        "target_index": target,
        "total": len(doc_paras),
    }
