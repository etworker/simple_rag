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

## 近期迭代（2026-08-12 ~ 08-16）

- **PDF 解析后端选型与新增 PyMuPDF 快路径**（08-16）：基于 opendataloader-bench（200 份真实 PDF）与中文真实文档实测，新增 doc_parser 的 **pymupdf** 后端（数字文本 PDF 秒级解析，实测比 pdfplumber 快约 2 倍；116 页手册 4.99s vs 9.75s）；select_backend auto 路由升级为：扫描件→MinerU、无框线表格→Docling（深度学习表格，TEDS 开源最强）、数字文本→PyMuPDF；详见 docs/PDF解析库对比与选型报告.md 与 temp/pdf_parsers_verification.md。
- **GPU 全链路加速**（08-16，现已改为环境自适应）：安装脚本自动选择互斥的 CPU/GPU 运行时。NVIDIA GPU 使用 fastembed-gpu/onnxruntime-gpu；Linux 可使用 faiss-gpu，Windows 因无 Faiss GPU wheel 使用 faiss-cpu；CPU 环境使用 fastembed/onnxruntime/faiss-cpu。MinerU/Magika 在 GPU 环境仍可能通过依赖元数据引入 `onnxruntime` distribution，安装脚本会校验实际导入是否包含 `CUDAExecutionProvider`，不允许静默回退 CPU。
- **默认解析后端切 auto**（08-17）：根据文档特征自动选择 PyMuPDF、Docling 或 MinerU；也可在配置中固定 `pdfplumber` 作为已验证的数字文本 PDF 稳定路径。
- **解析质量与版本对比准确度**（08-16）：解析缓存（SHA256+配置签名，重复解析秒级）；docling 碎尾合并 + 页眉前缀剥离（168→0 段）；版本对比精确文本优先配对 + removed/added 二次配对（改写归为修改）+ removed×modified 吸收（半句并入改写段落）+ 表格行不参与二次配对（保留真实行增删）+ 前缀规则（解析截断归细微）；fastembed parallel=None 修复显存泄漏/OOM；测试隔离根除 config.json 污染。详见 docs/设计文档.md §12.6-12.7 与 docs/文档解析与chunk切分说明.md。
- **锁定镜像安装**（08-17）：普通包默认走清华 PyPI 镜像；PyTorch 按互斥 extra 固定到官方 CPU/cu126 索引，避免后续 `uv sync` 覆盖设备版本；支持 `--pypi-index` 覆盖普通包镜像。
- **llm_chat**：后端异常挂 `status_code` / `is_network_error` 属性，`retry` 据此判断重试（不再正则解析异常文本）；公共字段初始化上提到 `_init_common`。
- **跨模块去重**：`version_diff.llm_util` 改用新增的 `llm_chat.ask_once_with_config`；`rag_server` 的 PDF 页数计算统一为 `get_pdf_page_count`（fitz）。
- **错误处理契约**：ChatSession 问答失败向上抛出；预审核任一 LLM/版本比较失败均标记为 `incomplete/error`，不得误报安全；版本差异过滤遇到不完整 LLM 响应时保守保留变更。
- **依赖管理重构**（08-14，08-17 更新）：四个模块使用根级 uv workspace/lock；安装脚本自动检测设备，并通过互斥 `cpu`/`gpu` extras 排除另一套运行时后同步全部 workspace 包。
- **Embedding 改 fastembed**（08-14）：`sentence-transformers` → `fastembed`（ONNX Runtime），`device_utils.py` 新增 `EmbeddingModel` 适配器，零 torch 依赖也能跑 embedding。
- **torch 策略**（08-15）：无 GPU 也安装 **CPU 版 torch** + 全量解析 extras —— docling / mineru 均可用（mineru 用 pipeline 模式，慢但能跑；已用 9 页真实 PDF 端到端验证，CPU 解析 86s）。mineru extra 改为 `mineru[pipeline,vlm]`（不再 `mineru[all]`，不装 lmdeploy/vllm）。
- 详细决策与原理见 [设计文档 §迭代记录](docs/设计文档.md)。

## 测试

各模块独立 `pytest`（uv workspace 下共用根 `.venv`），本次工作树验证结果：

```bash
cd doc_parser   && uv run pytest            # 77 passed
cd llm_chat     && uv run pytest            # 39 passed
cd version_diff && uv run pytest            # 124 passed
cd rag_server   && uv run pytest            # 75 passed, 5 skipped
```

> 注：`version_diff` 的部分版本对比用例依赖 `data/docx/v1|v2` 测试数据；缺失时会产生环境性失败。依赖真实外部服务或 GPU 的端到端用例可在相应环境中单独执行。
