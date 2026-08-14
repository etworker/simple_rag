# API 参考（API Reference）

> simple_rag 接口文档 ｜ 代码状态：2026-08 全量重构 + 08-12 ~ 08-15 迭代优化（详见 [设计文档 §迭代记录](设计文档.md)）
> 基础路径：服务运行于 `http://localhost:8000`

---

## 第一部分：HTTP API（FastAPI）

所有响应为 JSON；错误返回标准 `4xx/5xx` + `detail`。
> 所有端点统一以 `/api` 为前缀。文档管理、预审核路由挂在 `/api/documents` 下，问答挂在 `/api/qa` 下。

### 1. 文档管理（`routes/documents.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/documents/list` | 列出已入库文档（filename, doc_id, file_hash, paragraph_count, table_count, page_count, char_count, added_at, status）+ total |
| GET | `/api/documents/paragraphs?name=` | 已入库文档的段落列表（text, page, chapter, chapter_title, location） |
| GET | `/api/documents/review/paragraphs?file=` | 预审核中新文档的段落（尚未入库） |
| GET | `/api/documents/pdf?name=` | 返回原始 PDF 文件（`application/pdf`） |
| GET | `/api/documents/page?name=&page=1&highlight=` | 返回指定页 PNG（`image/png`） |
| GET | `/api/documents/info?name=` | 文档基本信息（page_count） |
| DELETE | `/api/documents/remove/{filename}` | 删除已入库文档 |
| POST | `/api/documents/clear` | 清空知识库（删除所有文档+缓存，不可恢复） |

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
| GET | `/api/qa/sessions/{session_id}` | 会话完整历史 |
| DELETE | `/api/qa/sessions/{session_id}` | 删除会话 |
| GET | `/api/qa/source?file=&location=` | 指定位置段落原文 |
| GET | `/api/qa/context?file=&index=0&radius=3` | 某段落前后上下文 |

### 3. 预审核（`routes/review.py`，挂载于 `/api/documents` 前缀）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/documents/review/upload`（`file=`, `choice=`） | 上传文档并启动预审核；同名不同内容先返回 `needs_choice`（overwrite/coexist） |
| GET | `/api/documents/review/active` | 当前活跃预审核任务（状态/进度/结果/旧版信息） |
| POST | `/api/documents/review/{task_id}/cancel` | 取消进行中的预审核 |
| GET | `/api/documents/review/{task_id}/progress` | **SSE** 实时进度流（`text/event-stream`） |
| POST | `/api/documents/review/{task_id}/confirm` | 人工确认入库 |
| POST | `/api/documents/review/{task_id}/reject` | 人工拒绝入库 |
| POST | `/api/documents/review/{task_id}/rerun` | 强制重跑（忽略缓存） |
| GET | `/api/documents/review/pdf?task_id=` | 预审核文件原始 PDF |
| GET | `/api/documents/review/page?task_id=&page=1&highlight=` | 预审核文件指定页 PNG |

**上传返回（新建）**
```json
{ "task_id": "a1b2c3d4e5f6", "filename": "规范.pdf", "file_hash": "..." }
```
**上传返回（同名需选择）**
```json
{ "needs_choice": true, "filename": "规范.pdf", "file_hash": "...",
  "existing": { "filename": "...", "doc_id": "...", "file_hash": "..." },
  "options": [ {"id": "overwrite", "label": "覆盖已有文档"}, {"id": "coexist", "label": "作为新版本并存"} ] }
```

### 4. 系统与运维（`main.py` 直接定义）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取当前配置（`config_store.to_dict()`） |
| POST | `/api/config` | 更新并保存配置 |
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
    prompt: str,
    llm_config: dict,           # {"provider","model","region","api_key_env","max_tokens",...}
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
