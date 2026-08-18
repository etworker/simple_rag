# version_diff — 文档差异检测引擎

语义配对 + 字级 diff + 规则预过滤 + LLM 矛盾判定 + 版本对比 + 统一冲突检测。

## 功能

- **跨文档预审核**：新文档入库前，自动与已有文档做语义配对 → 字级 diff → LLM 矛盾判定
- **版本对比**：同一文档新旧版本对比，输出全部 added/removed/modified 变更
- **冲突检测**：RAG 检索结果中两两段落矛盾检测（供问答引擎调用）
- **噪声过滤**：自动剔除修订日期、版本号、页码跟踪行等元数据噪声

## 安装

```bash
# 推荐：仓库根目录一键安装（uv workspace，自动装齐 4 模块 + 匹配 GPU 的 torch）
cd .. && python setup_env.py

# 或仅同步本模块（uv workspace 自动解析 doc_parser + llm_chat）
cd version_diff
uv sync
```

## 快速开始

### 预审核

```python
from version_diff import DiffEngine

engine = DiffEngine(
    config={
        "embedding": {"model": "BAAI/bge-small-zh-v1.5"},
        "llm": {"provider": "bedrock", "model": "zai.glm-4.7-flash"},
        "diff": {"similarity_threshold": 0.80},
    }
)
engine.add("a.pdf")
engine.add("b.pdf")
result = engine.pre_review("new.pdf")
if not result.is_safe:
    print(result.report())  # Markdown 报告
```

### 版本对比

```python
result = engine.version_compare("old.pdf", "new.pdf")
for change in result.changes:
    print(f"[{change.change_type}] {change.section} — {change.summary}")
```

### 冲突检测（供 RAG 问答）

```python
from version_diff import detect_conflicts

conflicts = detect_conflicts(
    [
        {"text": "每天备份一次", "source_file": "A.pdf", "location": "§2.1"},
        {"text": "每周备份一次", "source_file": "B.pdf", "location": "§3.2"},
    ],
    llm_config={"provider": "bedrock", "model": "zai.glm-4.7-flash"},
)
```

## API 参考

### `DiffEngine`

```python
engine = DiffEngine(config: dict | None = None)
```

| 方法 | 说明 |
|------|------|
| `add(filepath)` | 添加文档到已有库 |
| `pre_review(filepath, on_progress=, ...)` | 预审核新文档，返回 `DiffResult` |
| `version_compare(old, new, on_progress=)` | 版本对比，返回 `VersionDiffResult` |
| `check_conflicts(passages)` | 问答冲突检测，返回 `list[Inconsistency]` |
| `filter_cross_noise(changes)` | 跨文档版式噪声过滤 |

### 数据模型

| 类 | 说明 |
|----|------|
| `DiffResult` | 预审核结果（`inconsistencies`, `is_safe`, `report()`） |
| `Inconsistency` | 一处文档间矛盾（`point`, `doc_a_says`, `doc_b_says`） |
| `VersionDiffResult` | 版本对比结果（`changes`, `minor_changes`） |
| `VersionChange` | 一处版本变更（`change_type`, `old_text`, `new_text`, `summary`） |

### 其他公共 API

| 函数 | 说明 |
|------|------|
| `judge_pairs(pairs, ...)` | LLM 批量矛盾判定 |
| `detect_conflicts(passages, ...)` | 统一冲突检测 |
| `call_llm_json(prompt, llm_config)` | LLM 调用 + JSON 数组解析 |

## 配置

> embedding 基于 **fastembed（ONNX Runtime）**，`device_utils.EmbeddingModel` 统一封装，输出与 SentenceTransformer(normalize_embeddings=True) 等价。

```python
config = {
    "embedding": {
        "model": "BAAI/bge-small-zh-v1.5",
        "device": "auto",  # auto / cpu / cuda / mps
        "dtype": "auto",  # auto / float16 / bfloat16 / float32（无 torch 时回退为字符串）
    },
    "llm": {
        "provider": "bedrock",  # bedrock / openai
        "model": "zai.glm-4.7-flash",
        # 其余字段透传给 llm_chat 后端
    },
    "diff": {
        "similarity_threshold": 0.80,  # 段落配对阈值
        "top_k": 3,  # 每段检索候选数
        "batch_size": 5,  # LLM 批量大小
        "noise_filter": {...},  # 元数据噪声过滤
        "cross_noise_filter": {...},  # 跨文档版式噪声过滤
    },
    "judge": {
        "prompt_file": "",  # 自定义 prompt 文件
        "prompt_template": "",  # 自定义 prompt 字符串
    },
    "cache": {
        "vector_cache_dir": "",  # 向量缓存目录
    },
}
```

## 提示词

随包发布，位于 `src/version_diff/prompts/`：

| 文件 | 用途 |
|------|------|
| `consistency_judge.txt` | 跨文档矛盾判定 |
| `version_filter.txt` | 版本对比实质性变更过滤 |

可通过 `judge.prompt_file` / `judge.prompt_template` 覆盖。

## 测试

```bash
cd version_diff
uv run pytest
```

## 设计文档

详见 [docs/设计文档.md](docs/设计文档.md)。
