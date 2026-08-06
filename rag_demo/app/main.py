"""
RAG 文档问答系统 — FastAPI 主入口

启动:
    cd rag_demo
    uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

import logging
import os

# 禁用 HuggingFace 联网检查（必须在任何 HF/transformers 导入前设置）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


# 自动加载 .env（项目根目录或上级目录）
def _load_secrets():
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _project_dir = os.path.dirname(_this_dir)  # rag_demo/
    _parent_dir = os.path.dirname(_project_dir)  # simple_rag/
    for path in [
        os.path.join(_project_dir, ".env"),  # rag_demo/.env
        os.path.join(_parent_dir, ".env"),  # simple_rag/.env
        os.path.join(_project_dir, "secrets.env"),  # 旧命名兼容
        os.path.join(_parent_dir, "secrets.env"),  # 旧命名兼容
    ]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
            break  # 只加载第一个找到的


_load_secrets()

import asyncio

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket, WebSocketDisconnect

from app.routes import documents, qa, review
from app.services.chat_history import ChatHistoryStore
from app.services.config_store import ConfigStore
from app.services.doc_store import DocStore
from app.services.qa_engine import QAEngine

# 日志
log = logging.getLogger("rag_demo")
# 缓存根目录（可配置，默认 ~/.simple_rag/）
_CACHE_ROOT = os.path.join(os.path.expanduser("~"), ".simple_rag")
# 日志同时输出到文件和控制台（追加模式，保留历史）
LOG_FILE = os.path.join(_CACHE_ROOT, "logs", "app.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # 访问日志太多
logging.getLogger("pdfminer").setLevel(logging.WARNING)  # pdfminer 解析日志极多
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

# 每次启动打印分隔线，方便区分不同运行会话
import time as _time

_session_sep = "=" * 80
log.info(_session_sep)
log.info(f"🚀 服务启动 @ {_time.strftime('%Y-%m-%d %H:%M:%S')}")
log.info(f"   日志文件: {LOG_FILE}")
log.info(_session_sep)

# 路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 初始化配置
config_store = ConfigStore(config_path=CONFIG_PATH)

# 缓存目录（可配置，默认 ~/.simple_rag/）
CACHE_DIR = config_store.get("cache.base_dir", _CACHE_ROOT)
os.makedirs(CACHE_DIR, exist_ok=True)
UPLOAD_DIR = config_store.get("upload_dir", os.path.join(CACHE_DIR, "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)
log.info(f"缓存目录: {CACHE_DIR}")
log.info(f"上传目录: {UPLOAD_DIR}")

# 初始化服务
# 给 DocStore 传入绝对路径，避免 CWD 依赖
doc_config = config_store.to_dict()
doc_config["persist_dir"] = os.path.join(CACHE_DIR, "doc_store")
doc_config["parse_cache_dir"] = os.path.join(CACHE_DIR, "parse_cache")
doc_config["vector_cache_dir"] = os.path.join(CACHE_DIR, "vector_cache")
doc_store = DocStore(doc_config)
history_store = ChatHistoryStore(history_dir=os.path.join(CACHE_DIR, "chat_history"))
qa_engine = QAEngine(doc_store, config_store, history_store)

# 初始化路由
documents.init(doc_store, config_store, UPLOAD_DIR, CACHE_DIR)
qa.init(qa_engine)

# FastAPI app
app = FastAPI(title="RAG 文档问答系统", version="0.1.0")

# 静态文件
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# 注册路由
app.include_router(documents.router, prefix="/api/documents", tags=["文档管理"])
app.include_router(review.router, prefix="/api/documents", tags=["预审核"])
app.include_router(qa.router, prefix="/api/qa", tags=["问答"])


# 配置 API
@app.get("/api/config")
async def get_config():
    """获取当前配置"""
    return config_store.to_dict()


@app.post("/api/config")
async def update_config(updates: dict):
    """更新配置"""
    config_store.update(updates)
    config_store.save()
    return {"message": "配置已更新"}


# 首页
@app.get("/", response_class=HTMLResponse)
async def index():
    """返回主页面（禁用缓存，确保始终加载最新版本）"""
    html_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        # 加版本号注释，方便确认是否加载了最新版本
        return HTMLResponse(
            content=content,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return """
<html><body>
<h1>RAG 文档问答系统</h1>
<p>请将 index.html 放入 app/static/ 目录</p>
<p>API 文档: <a href="/docs">/docs</a></p>
</body></html>
"""


# ============================================================
# WebSocket 实时日志
# ============================================================


class WebSocketLogHandler(logging.Handler):
    """将日志推送到所有已连接的 WebSocket 客户端"""

    clients: list = []
    # 保留最近 200 条日志，新连接时回放
    history: list = []

    def emit(self, record):
        msg = self.format(record)
        # 缓存历史
        WebSocketLogHandler.history.append(msg)
        if len(WebSocketLogHandler.history) > 200:
            WebSocketLogHandler.history.pop(0)
        for ws_queue in self.clients[:]:
            try:
                ws_queue.put_nowait(msg)
            except Exception:
                pass


_ws_handler = WebSocketLogHandler()
_ws_handler.setLevel(logging.DEBUG)
_ws_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(name)s | %(message)s", datefmt="%H:%M:%S")
)
# 挂到 root logger
logging.getLogger().addHandler(_ws_handler)


@app.get("/api/logs/tail")
async def tail_logs(lines: int = 100):
    """读取日志文件最后 N 行"""
    if not os.path.exists(LOG_FILE):
        return {"lines": []}
    try:
        # 从文件末尾读取，避免读取整个大文件
        chunk_size = lines * 500  # 估算每行 ~500 字节
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)  # 到末尾
            file_size = f.tell()
            read_pos = max(0, file_size - chunk_size)
            f.seek(read_pos)
            data = f.read().decode("utf-8", errors="ignore")
        result_lines = data.split("\n")
        # 如果不是从头开始读，第一行可能不完整，去掉
        if read_pos > 0:
            result_lines = result_lines[1:]
        return {"lines": [l.rstrip() for l in result_lines[-lines:] if l.strip()]}
    except Exception as e:
        return {"lines": [f"读取日志失败: {e}"]}


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    queue = asyncio.Queue()
    # 先发送历史日志
    for msg in WebSocketLogHandler.history:
        await websocket.send_text(msg)
    WebSocketLogHandler.clients.append(queue)
    try:
        while True:
            msg = await queue.get()
            await websocket.send_text(msg)
    except WebSocketDisconnect:
        pass
    finally:
        WebSocketLogHandler.clients.remove(queue)
