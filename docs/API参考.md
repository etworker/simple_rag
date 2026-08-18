# API 参考（API Reference）

> simple_rag 接口文档 ｜ 代码状态：2026-08 全量重构 + 08-12 ~ 08-15 迭代优化（详见 [设计文档 §迭代记录](设计文档.md)）
> 基础路径：服务运行于 `http://localhost:8000`

---

## 第一部分：HTTP API（FastAPI）

常规业务响应为 JSON，文件预览返回 PDF/PNG，预审核进度使用 SSE，日志流使用 WebSocket；错误返回标准 `4xx/5xx` + `detail`。
> 所有端点统一以 `/api` 为前缀。文档管理、预审核路由挂在 `/api/documents` 下，问答挂在 `/api/qa` 下。

### 1. 文档管理（`routes/documents.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/documents/list` | 列出已入库文档（filename, doc_id, file_hash, paragraph_count, table_count, page_count, char_count, added_at, status）+ total |
| GET | `/api/documents/paragraphs?name=` | 已入库文档的段落列表（text, page, chapter, chapter_title, location） |
| GET | `/api/documents/review/paragraphs?file=&task_id=` | 预审核中新文档的段落（尚未入库；可按任务 ID 精确获取） |
| GET | `/api/documents/pdf?doc_id=&name=` | 返回原始 PDF 文件（`application/pdf`）；`doc_id` 可精确预览历史版本 |
| GET | `/api/documents/page?doc_id=&name=&page=1&highlight=` | 返回指定页 PNG（`image/png`） |
| GET | `/api/documents/info?doc_id=&name=` | 文档基本信息（page_count），支持 active/inactive |
| POST | `/api/documents/primary`（`doc_id=`） | 将历史版本设为同族唯一当前 active/primary，其他版本转 inactive |
| DELETE | `/api/documents/remove/{filename}` | 删除已入库文档 |
| POST | `/api/documents/label`（`doc_id=`, `label=`） | 更新文档补充描述/版本标签 |
| POST | `/api/documents/clear` | 清空知识库中的文档、段落、向量索引及相关解析/向量/页面/预审核缓存；不删除 chat history、logs 或上传目录 |

### 2. 问答（`routes/qa.py`）

**POST `/api/qa/ask`**
```json
请求：{ "question": "备份频率是多少？", "session_id": "default" }
响应：{
  "answer": "...",
  "sources": [ {"file": "...", "location": "...", "text": "..."} ],
  "conflicts": [ {"point": "...", "doc_a_file": "...", "doc_a_says": "...", "doc_others": [...]} ],
  "has_conflicts": false
}
```
错误：问题为空 → 400；LLM 不可用 → 503；其他 → 500。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/qa/reset?session_id=default` | 重置对话历史 |
| GET | `/api/qa/sessions?limit=50` | 列出问答会话 |
| DELETE | `/api/qa/sessions` | 清空全部问答会话历史与当前进程中的会话上下文 |
| DELETE | `/api/qa/sessions/{session_id}` | 删除指定问答会话 |
| GET | `/api/qa/sessions/{session_id}` | 会话完整历史 |
| GET | `/api/qa/source?file=&location=` | 指定位置段落原文 |
| GET | `/api/qa/context?file=&index=0&radius=3` | 某段落前后上下文 |

### 3. 预审核（`routes/review.py`，挂载于 `/api/documents` 前缀）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/documents/upload`（`file=`, `choice=`, `label=`） | 上传文档并启动预审核；同名不同内容未给 choice 时返回 `needs_choice`，响应选项为 `coexist` / `new_primary`；确认阶段的 `mode` 为 `keep_current` / `new_primary`，`overwrite` 为兼容输入 |
| GET | `/api/documents/review/active` | 当前活跃预审核任务（状态/进度/结果/旧版信息） |
| POST | `/api/documents/review/{task_id}/pause` | 暂停逐文档比较 |
| POST | `/api/documents/review/{task_id}/resume` | 继续暂停的比较 |
| POST | `/api/documents/review/{task_id}/cancel` | 取消进行中的预审核 |
| POST | `/api/documents/review/{task_id}/label`（`label=`） | 更新待审核文档标签 |
| GET | `/api/documents/review/{task_id}/progress` | **SSE** 实时进度流（`text/event-stream`） |
| POST | `/api/documents/review/{task_id}/confirm`（`mode=keep_current|new_primary`） | 人工确认入库；同名版本需选择保留旧主版本或切换新版本 |
| POST | `/api/documents/review/{task_id}/reject` | 人工拒绝入库 |
| POST | `/api/documents/review/{task_id}/rerun` | 强制重跑（忽略缓存） |
| GET | `/api/documents/review/pdf?task_id=` | 预审核文件原始 PDF |
| GET | `/api/documents/review/page?task_id=&page=1&highlight=` | 预审核文件指定页 PNG |
| GET | `/api/documents/review/info?task_id=` | 预审核文件页数信息 |
| GET | `/api/documents/review/old/info?task_id=&doc_id=` | 对比文档页数信息 |
| GET | `/api/documents/review/old/page?task_id=&page=1&doc_id=` | 对比文档指定页 PNG |

**上传返回（新建）**
```json
{ "task_id": "a1b2c3d4e5f6", "filename": "规范.pdf", "file_hash": "..." }
```
**上传返回（同名需选择）**
```json
{ "needs_choice": true, "filename": "规范.pdf", "file_hash": "...",
  "existing": { "filename": "...", "doc_id": "...", "file_hash": "..." },
  "options": [
    { "id": "coexist", "label": "保留当前版本并保存新版本" },
    { "id": "new_primary", "label": "确认后使用新版本" }
  ] }
```
> 上传接口返回的 `coexist` 表示保留当前主版本并继续审核新版本，`new_primary` 表示确认时倾向切换新版本；确认接口再用 `mode=keep_current` 或 `mode=new_primary` 作最终选择。新文档在上传/审核阶段尚未写入正式文档元数据；确认入库时，有旧主版本的文档先以 `inactive` 写入，`new_primary` 再切换同族唯一 `active`/`primary`，`keep_current` 则保留旧主版本。`overwrite` 仍可由旧客户端传入，但不表示删除旧文件；完整相同 SHA-256 内容会被拒绝重复入库。

### 4. 系统与运维（`main.py` 直接定义）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取当前配置（`config_store.to_dict()`） |
| GET | `/api/config/schema` | 获取配置项中文描述 |
| POST | `/api/config` | 保存配置；返回 `restart_required=true`，embedding 变化还会返回 `reset_knowledge_base_required=true` |
| GET | `/api/logs/tail?lines=100` | 读取日志文件末尾 N 行 |
| WS | `/ws/logs` | WebSocket 实时日志流（新连接先回放最近 200 条） |
| GET | `/` | 首页（返回 `app/static/index.html`，禁用缓存） |
| GET | `/static/*` | 静态资源（`app.js` / `style.css` / 图片等） |

> 完整请求/响应字段以 `http://localhost:8000/docs` 中自动生成的 OpenAPI 为准。

---

## 第二部分：Python 模块 API

### 1. doc_parser

```python
from doc_parser import parse, Document, Paragraph, Table

doc: Document = parse(filepath: str, config: dict = None) -> Document
# doc.paragraphs: list[Paragraph]
# doc.tables:     list[Table]

# PDF 后端（config["extract"]["backend"]；Web 配置在 pre_review.parse_backend）：
#   "auto"      默认智能路由：扫描件→mineru，无框线表格→docling，数字文本→pymupdf；不可用时降级 pdfplumber
#   "pdfplumber" 规则后端：可显式固定，适合需要稳定文本段落/定制规则的场景
#   "pymupdf"    快路径：PyMuPDF 数字文本解析
#   "docling"    深度学习版面与表格（docling_device: auto/cuda/cpu）
#   "mineru"     扫描件/图片 PDF 的 OCR/VLM 或 pipeline 路径

# Paragraph / Table 均提供 to_dict() / from_dict() 与只读 location 属性
p = Paragraph(text="...", page=3, chapter="2.2", chapter_title="备份策略", source_file="a.pdf")
d = p.to_dict(); p2 = Paragraph.from_dict(d)
```

### 2. llm_chat

```python
from llm_chat import ask_once, ask_once_with_config, resolve_llm_profile, ChatSession

resp: str = ask_once(
    prompt: str,
    system_prompt: str = "",
    backend: str = "",          # "bedrock" | "openai"（空=默认 bedrock）
    model: str = "",            # 空=各后端默认模型
    **kwargs,                   # region / base_url / api_key_env / api_key / max_tokens / timeout / max_retries / retry_backoff
) -> str

# 从配置字典一次性调用（provider→后端，其余透传），消除调用方手写逐字段透传
resp: str = ask_once_with_config(
    llm_config: dict,           # {"provider","model","region","api_key_env","max_tokens",...}
    prompt: str,
    system_prompt: str = "",
) -> str

# 从 profiles+routing 解析某用途的完整 LLM 配置（ConfigStore 复用同一实现）
profile: dict = resolve_llm_profile(profiles: dict, routing: dict, use_case: str) -> dict

# 多轮对话（方法名为 ask，非 send）
sess = ChatSession(model="zai.glm-4.7-flash")
sess.ask("你好")   # 失败向上抛出（RuntimeError），由上层转为 HTTP 错误，不返回 [错误] 文本
```

可用后端（`llm_chat/backends/`）：`bedrock`（别名 `bedrock_converse`）、`openai`。

后端调用异常统一抛 `RuntimeError`，并附带属性以便重试逻辑判断（不再正则解析异常文本）：
- `e.status_code` → HTTP 状态码（仅服务端错误，如 503）
- `e.is_network_error` → 是否网络/超时类错误（布尔）

`ChatSession.ask` 不再把失败吞成 `"[错误] ..."` 字符串返回（旧行为会把错误当正常答案返回给用户），而是向上抛出，由 FastAPI 路由统一映射为 503（LLM 不可用）/ 500（内部错误）。

### 3. version_diff

```python
from version_diff import DiffEngine, Config, judge_pairs, detect_conflicts, call_llm_json

# —— 引擎 ——
engine = DiffEngine(config: dict = None)
engine.add(filepath: str)                                  # 加载已有文档到向量库
result: DiffResult = engine.pre_review(
    filepath: str,
    on_progress: Callable = None,   # fn(step, percent, message)
)                                      # step: parsing/embedding/searching/diffing/judging/done
vresult: VersionDiffResult = engine.version_compare(
    old_filepath: str, new_filepath: str, on_progress: Callable = None
)
incons: list[Inconsistency] = engine.check_conflicts(retrieved_passages: list[dict])

# —— 配置 ——
# diff.noise_filter：可配置的「版本管理元数据噪声」过滤（默认开启）
# 对 version_compare 的 added/removed 段落，剥离下列正则后为空 → 判为纯元数据噪声，
# 归入 VersionDiffResult.minor_changes（而非 changes）。默认值通用、可被覆盖。
cfg = Config.from_dict({
    "embedding": {"model": "BAAI/bge-small-zh-v1.5"},  # 基于 fastembed/ONNX，零 torch 依赖
    "llm": {"provider": "bedrock", "model": "zai.glm-4.7-flash"},
    "diff": {
        "similarity_threshold": 0.80, "top_k": 3, "batch_size": 5,
        "noise_filter": {
            "enabled": True,
            "patterns": [
                r"修订日期\s*[：:]\s*\S+",
                r"(?:^\s*|\b)\d{4}[-./]\s*\d{1,2}[-./]\s*\d{1,2}\s*(?:$|\b)",
                r"(?:R\d+-\d{2,}|BK-J-\d+|版次\s*[：:]\s*\S+)",
            ],
        },
    },
})

# —— 公共判定接口（鸭子类型，外部无需感知 TextDiffItem） ——
pairs = [{"a": {"text": "...", "source_file": "A", "location": "第1页"},
          "b": {"text": "...", "source_file": "B", "location": "第1页"}}]
judged: list[dict] | None = judge_pairs(pairs, llm_config, judge_config=None)
# 每项: {"index", "inconsistent", "point", "doc_a_says", "doc_b_says"}；失败返回 None

# —— 统一冲突检测 ——
conflicts: list[dict] = detect_conflicts(
    passages: list[dict],                 # 每项 {text, source_file, location, score?}
    llm_config: dict | None = None,
    judge_config: dict | None = None,
    cd_config: dict | None = None,        # {min_score, min_similarity, max_similarity}
)
# 每项: {point, doc_a_file, doc_a_location, doc_a_says, doc_others:[{file, location, says}]}

# —— LLM + JSON 抽取（带重试） ——
data: list | None = call_llm_json(
    prompt: str, llm_config: dict,
    max_retries: int = 2, retry_backoff: float = 1.0,
)
```

**返回模型**

`VersionChange`：`change_type("modified"|"added"|"removed")`, `section`, `location`, `old_text`, `new_text`, `summary`, `similarity`.

`VersionDiffResult`：`changes: list[VersionChange]`, `old_paragraph_count`, `new_paragraph_count`.

`Inconsistency`：`point`, `doc_a_file`, `doc_a_location`, `doc_a_says`, `doc_b_file`, `doc_b_location`, `doc_b_says`, `similarity`.

`DiffResult`：`inconsistencies: list[Inconsistency]`, `total_candidates`, `rule_filtered`, `llm_judged`；含 `is_safe` 属性、`report()`（Markdown）、`to_dict()`。

### 4. 提示词（prompts）
位于 `version_diff/src/version_diff/prompts/`：
- `consistency_judge.txt`：跨文档矛盾判定（供 `judge_pairs`）。
- `version_filter.txt`：版本对比的实质性变更过滤（供 `version_compare`）。

模板含 `{count}` / `{items}` 占位符，运行时 `.format(...)` 填充；JSON 示例用 `{{`/`}}` 转义。可通过 `judge.prompt_file` / `judge.prompt_template` 覆盖。
