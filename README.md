# simple_rag

一个检索增强生成（RAG）系统，内置**文档解析**、**跨文档一致性预审核**、**版本对比**与**对话问答**能力，专门面向企业多版本文档场景。代码按职责拆分为 4 个独立模块。

---

## 文档导航

| 文档 | 内容 |
|------|------|
| [需求文档](docs/需求文档.md) | 产品定位、用户角色、功能/非功能需求、使用场景 |
| [架构文档](docs/架构文档.md) | 四模块划分、依赖方向、技术栈、两条数据流、部署拓扑 |
| [设计文档](docs/设计文档.md) | 关键设计决策与原理（语义配对、规则表、统一冲突检测、配置分层等）+ 时序图 |
| [使用手册](docs/使用手册.md) | 安装、配置（`.env` / `config.json`）、启动、Web 使用、命令行调用、排错 |
| [用户说明：文档入库与内容对比](docs/客户说明-文档入库与内容对比.md) | 面向非技术用户，详细解释解析、chunk、Embedding、版本差异、表格比较、人工确认、RAG 问答和常见问题 |
| [API 参考](docs/API参考.md) | FastAPI 全部端点 + Python 模块级 API 签名与返回 |
| [开发指南](docs/开发指南.md) | 仓库布局、测试命令、扩展方式、代码约定、提交前检查 |
| [Embedding 模型选型](docs/Embedding模型选型.md) | 中文 RAG 向量模型：fastembed 候选清单（实测/推测标注）、推荐与切换路径 |
| [AWS 目标部署方案（中国区）](docs/快速原型部署方案.md) | 核心功能验证完成后的目标架构：g5.2xlarge + 托管向量库/数据库/历史/认证 |

---

## 模块边界

| 模块 | 职责 | 关键入口 |
|------|------|----------|
| `doc_parser` | 文档解析（PDF / Word）→ 段落 + 表格 + 定位信息；PDF 多后端（pdfplumber / **pymupdf** / mineru / docling） | `parse(filepath, config)`，`Paragraph`/`Table`/`Document` 模型 |
| `llm_chat` | LLM 调用抽象（bedrock / openai 等多后端）、重试、对话会话 | `ask_once(prompt, backend, ...)`、`ask_once_with_config(llm_config, prompt, ...)`、`resolve_llm_profile(profiles, routing, use_case)`、`ChatSession` |
| `version_diff` | 差异检测引擎：跨文档语义检索 + 字级 diff + 规则预过滤 + LLM 矛盾判定 + 版本对比 + 统一冲突检测 | `DiffEngine`（add / pre_review / version_compare / check_conflicts）、`judge_pairs`、`detect_conflicts`、`call_llm_json` |
| `rag_server` | FastAPI 应用：上传、预审核任务编排、RAG 问答、冲突检测、Web UI | `app/main.py`，`app/services/*`，`app/routes/*` |

依赖方向（无循环依赖）：

```
┌────────────┐     ┌──────────┐
│ doc_parser │     │ llm_chat │
└──┬─────┬───┘     └──┬────┬──┘
   │     └──────┬─────┘    │
   │            ▼           │
   │     ┌──────────────┐  │
   │     │ version_diff │  │
   │     └──────┬───────┘  │
   │            │           │
   └─────┐     │     ┌─────┘
         ▼     ▼     ▼
      ┌──────────────────┐
      │    rag_server    │
      └──────────────────┘
```

- `version_diff` 依赖 `doc_parser`（解析）与 `llm_chat`（判定）。
- `rag_server` 依赖其余三者。
- 生产代码无 `sys.path` hack；测试里为隔离会临时注入路径。

---

## 快速开始

> 本项目用 **uv workspace** 管理：根目录 `pyproject.toml` 声明 4 个模块成员 + 根级 `uv.lock`，四个模块统一在一个 `.venv` 中。环境配置推荐使用根目录的 `setup_env.sh`（Linux/macOS）或 `setup_env.bat`（Windows）；两者调用同一个 `setup_env.py`，自动处理 uv、CPU/GPU 运行时和项目依赖。

```bash
# 1. 配置全部依赖（自动检测 CPU/NVIDIA GPU；缺少 uv 时会尝试用 pip 安装 uv）
#    Linux / macOS
./setup_env.sh
#    Windows：setup_env.bat
#    也可直接调用跨平台核心脚本：python setup_env.py
#    可用 --device cpu|gpu 手动覆盖自动检测
# ./setup_env.sh --device cpu
# ./setup_env.sh --device gpu

# 2. 配置环境变量（LLM 凭证等）
#    复制并修改 .env（已有示例），或参考 docs/使用手册.md 第 2 节
cp rag_server/config.example.json rag_server/config.json

# 3. 启动（自动 uv sync + 加载 .env + 离线变量 + uvicorn）
./start.sh          # Linux / macOS
# start.bat         # Windows

# 4. 访问
#    Web UI:      http://localhost:8000
#    API 文档:    http://localhost:8000/docs
```

也可显式同步 CPU 或 GPU 运行时（两者互斥）：

```bash
uv sync --package rag-server --extra cpu       # CPU / Windows 通用
uv sync --package rag-server --extra gpu       # NVIDIA GPU；Linux 可启用 Faiss GPU
```

### 命令行 / 脚本化验证版本差异 + LLM 归纳

```bash
cd version_diff
uv run python examples/verify_version_diff.py <旧版.pdf> <新版.pdf> --model zai.glm-4.7-flash
# 不传路径时默认使用 data/pdf 下的 IT运维管理规范 v1/v2
```

---

## 核心能力

1. **版本差异识别**：同一文档新旧版本 → 语义配对 + 字级 diff + 表格对齐，识别新增/删除/修改（已用真实多版本 PDF 验证：v1→v2 识别 35 处，R5-18→R5-22 识别 114 处）。
2. **LLM 归纳**：先用 LLM 过滤"实质性变更"（剔除纯格式/标点噪音），再生成结构化中文摘要（变更类别 / 实质性摘要 / 整体评估）。
3. **跨文档一致性预审核**：新文档入库前自动与已有文档做矛盾检测，辅助人工决策。
4. **带冲突标注的问答**：检索到跨文档矛盾时在答案中醒目标注来源。

---

## 配置

- 项目为 **uv workspace**：根 `pyproject.toml` 声明 `members`（doc_parser / llm_chat / version_diff / rag_server），各模块 `pyproject.toml` 通过 `[tool.uv.sources] ... { workspace = true }` 引用兄弟包，根级 `uv.lock` 统一锁版本。
- `rag_server/config.json` 是**本地配置，不入库**（已被 `.gitignore` 排除）。新克隆时复制 `config.example.json` 为 `config.json` 再修改。
- LLM / embedding 的默认值集中在 `llm_chat/src/llm_chat/defaults.py` 与 `version_diff/src/version_diff/config.py`，可用环境变量覆盖（详见 [使用手册](docs/使用手册.md) 与 [开发指南](docs/开发指南.md)）。
- `rag_server` 采用 **profiles + routing** 配置：在 `config.json` 的 `llm_profiles` 定义多模型，在 `llm_routing` 中按用途（qa / pre_review / conflict_detection）路由。
- **Embedding 基于 fastembed（ONNX Runtime，零 torch 依赖）**：`version_diff/device_utils.py::EmbeddingModel` 统一封装，输出与 `SentenceTransformer(normalize_embeddings=True)` 等价（L2 归一化），默认模型 `BAAI/bge-small-zh-v1.5`。

## 提示词（prompts）

- `version_diff/src/version_diff/prompts/` 存放随包发布的提示词模板：
  - `consistency_judge.txt`：跨文档矛盾判定。
  - `version_filter.txt`：版本对比的实质性变更过滤。
- 模板含 `{count}` / `{items}` 占位符，运行时由代码 `.format(...)` 填充；JSON 示例用 `{{`/`}}` 转义。可通过 `judge.prompt_file` / `judge.prompt_template` 覆盖。

---

## 测试

各模块独立 `pytest`（uv workspace 下共用根 `.venv`），本次工作树验证结果：

```bash
cd doc_parser   && uv run pytest            # 77 passed
cd llm_chat     && uv run pytest            # 39 passed
cd version_diff && uv run pytest            # 124 passed
cd rag_server   && uv run pytest            # 75 passed, 5 skipped
```

> 注：`version_diff` 的部分版本对比用例依赖 `data/docx/v1|v2` 测试数据；缺失时会产生环境性失败。依赖真实外部服务或 GPU 的端到端用例可在相应环境中单独执行。
