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
| [快速原型部署方案（AWS 中国区）](docs/快速原型部署方案.md) | 单卡 A10g 原型：g5.2xlarge + 托管向量库/数据库/对话历史/用户管理 + 成本预估 |

---

## 模块边界

| 模块 | 职责 | 关键入口 |
|------|------|----------|
| `doc_parser` | 文档解析（PDF / Word）→ 段落 + 表格 + 定位信息；PDF 多后端（pdfplumber / **pymupdf** / mineru / docling） | `parse(filepath, config)`，`Paragraph`/`Table`/`Document` 模型 |
| `llm_chat` | LLM 调用抽象（bedrock / openai 等多后端）、重试、对话会话 | `ask_once(prompt, backend, ...)`、`ask_once_with_config(prompt, llm_config, ...)`、`resolve_llm_profile(profiles, routing, use_case)`、`ChatSession` |
| `version_diff` | 差异检测引擎：跨文档语义检索 + 字级 diff + 规则预过滤 + LLM 矛盾判定 + 版本对比 + 统一冲突检测 | `DiffEngine`（add / pre_review / version_compare / check_conflicts）、`judge_pairs`、`detect_conflicts`、`call_llm_json` |
| `rag_demo` | FastAPI 应用：上传、预审核任务编排、RAG 问答、冲突检测、Web UI | `app/main.py`，`app/services/*`，`app/routes/*` |

依赖方向（无循环依赖）：

```
doc_parser  ─┐
             ├─► version_diff ─► rag_demo
llm_chat   ─┘        │
                     └─► rag_demo
```

- `version_diff` 依赖 `doc_parser`（解析）与 `llm_chat`（判定）。
- `rag_demo` 依赖其余三者。
- 生产代码无 `sys.path` hack；测试里为隔离会临时注入路径。

---

## 快速开始

> 本项目用 **uv workspace** 管理：根目录 `pyproject.toml` 声明 4 个模块成员 + 根级 `uv.lock`，四个模块统一在一个 `.venv` 中。安装推荐用根级脚本 `scripts/install_system.py`（自动检测 GPU 安装匹配的 torch 构建后统一 `uv sync`）。

```bash
# 1. 安装全部依赖（4 模块 + dev/docling/mineru extras + 匹配 GPU 的 torch）
#    有 NVIDIA GPU → CUDA 版 torch；无 GPU → CPU 版 torch（docling/mineru 均可用）
#    默认走清华 PyPI 镜像（AWS 宁夏/国内网络友好）；可用 --pypi-index/--torch-index 覆盖
python scripts/install_system.py              # 或用 uv run --no-project python scripts/install_system.py
#   python scripts/install_system.py --sync-only                    # 跳过 torch 仅 uv sync
#   python scripts/install_system.py --pypi-index https://mirrors.aliyun.com/pypi/simple  # 换阿里云镜像

# 2. 配置环境变量（LLM 凭证等）
#    复制并修改 .env（已有示例），或参考 docs/使用手册.md 第 2 节
cp rag_demo/config.example.json rag_demo/config.json

# 3. 启动（自动 uv sync + 加载 .env + 离线变量 + uvicorn）
./start.sh          # Linux / macOS
# start.bat         # Windows

# 4. 访问
#    Web UI:      http://localhost:8000
#    API 文档:    http://localhost:8000/docs
```

也可只同步某个模块（uv workspace 自动解析兄弟包）：

```bash
uv sync --project rag_demo       # 仅同步 rag_demo 所需（含三个兄弟包）
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

- 项目为 **uv workspace**：根 `pyproject.toml` 声明 `members`（doc_parser / llm_chat / version_diff / rag_demo），各模块 `pyproject.toml` 通过 `[tool.uv.sources] ... { workspace = true }` 引用兄弟包，根级 `uv.lock` 统一锁版本。
- `rag_demo/config.json` 是**本地配置，不入库**（已被 `.gitignore` 排除）。新克隆时复制 `config.example.json` 为 `config.json` 再修改。
- LLM / embedding 的默认值集中在 `llm_chat/src/llm_chat/defaults.py` 与 `version_diff/src/version_diff/config.py`，可用环境变量覆盖（详见 [使用手册](docs/使用手册.md) 与 [开发指南](docs/开发指南.md)）。
- `rag_demo` 采用 **profiles + routing** 配置：在 `config.json` 的 `llm_profiles` 定义多模型，在 `llm_routing` 中按用途（qa / pre_review / conflict_detection）路由。
- **Embedding 基于 fastembed（ONNX Runtime，零 torch 依赖）**：`version_diff/device_utils.py::EmbeddingModel` 统一封装，输出与 `SentenceTransformer(normalize_embeddings=True)` 等价（L2 归一化），默认模型 `BAAI/bge-small-zh-v1.5`。

## 提示词（prompts）

- `version_diff/src/version_diff/prompts/` 存放随包发布的提示词模板：
  - `consistency_judge.txt`：跨文档矛盾判定。
  - `version_filter.txt`：版本对比的实质性变更过滤。
- 模板含 `{count}` / `{items}` 占位符，运行时由代码 `.format(...)` 填充；JSON 示例用 `{{`/`}}` 转义。可通过 `judge.prompt_file` / `judge.prompt_template` 覆盖。

---

## 近期迭代（2026-08-12 ~ 08-16）

- **PDF 解析后端选型与新增 PyMuPDF 快路径**（08-16）：基于 opendataloader-bench（200 份真实 PDF）与中文真实文档实测，新增 doc_parser 的 **pymupdf** 后端（数字文本 PDF 秒级解析，实测比 pdfplumber 快约 2 倍；116 页手册 4.99s vs 9.75s）；select_backend auto 路由升级为：扫描件→MinerU、无框线表格→Docling（深度学习表格，TEDS 开源最强）、数字文本→PyMuPDF；详见 docs/PDF解析库对比与选型报告.md 与 temp/pdf_parsers_verification.md。
- **GPU 全链路加速**（08-16）：faiss-cpu → faiss-gpu、embedding 推理经 onnxruntime-gpu（CUDA provider）走 GPU；torch CUDA 构建 + docling docling_device=cuda 实测 0.56s/页（116 页手册 64s，表格识别 29 张 vs 规则后端 17 张）。
- **国内镜像安装**（08-16）：scripts/install_system.py 默认走清华 PyPI 镜像（AWS 宁夏/国内网络友好），支持 --pypi-index / --torch-index 覆盖；UV_DEFAULT_INDEX 让 uv sync 也走同一镜像。
- **llm_chat**：后端异常挂 `status_code` / `is_network_error` 属性，`retry` 据此判断重试（不再正则解析异常文本）；公共字段初始化上提到 `_init_common`。
- **跨模块去重**：`version_diff.llm_util` 改用新增的 `llm_chat.ask_once_with_config`；`rag_demo` 的 PDF 页数计算统一为 `get_pdf_page_count`（fitz）。
- **错误处理契约**：`ChatSession.ask` 失败改为向上抛出（不再把 `"[错误]..."` 当答案返回），`/api/qa/ask` 将 LLM 不可用映射为 503。
- **依赖管理重构**（08-14）：四个独立模块改为 **uv workspace**（根 pyproject.toml + 根 uv.lock），模块间依赖用 `[tool.uv.sources] { workspace = true }`，新增根级一键安装脚本 `scripts/install_system.py`（自动检测 GPU → 装匹配 torch 构建 → `uv sync --all-extras`）。
- **Embedding 改 fastembed**（08-14）：`sentence-transformers` → `fastembed`（ONNX Runtime），`device_utils.py` 新增 `EmbeddingModel` 适配器，零 torch 依赖也能跑 embedding。
- **torch 策略**（08-15）：无 GPU 也安装 **CPU 版 torch** + 全量 extras —— docling / mineru 均可用（mineru 用 pipeline 模式，慢但能跑；已用 9 页真实 PDF 端到端验证，CPU 解析 86s）。mineru extra 改为 `mineru[pipeline,vlm]`（不再 `mineru[all]`，不装 lmdeploy/vllm）。
- 详细决策与原理见 [设计文档 §迭代记录](docs/设计文档.md)。

## 测试

各模块独立 `pytest`（uv workspace 下共用根 `.venv`）：

```bash
cd doc_parser   && uv run pytest            # 77 passed（含新增 pymupdf 后端测试）
cd llm_chat     && uv run pytest            # 39 passed
cd version_diff && uv run pytest            # 96 passed（data/docx 缺失时部分用例环境性失败）
cd rag_demo     && uv run pytest            # 58 passed, 5 skipped（-k "not gpu and not e2e" 跳过 GPU/端到端）
```

> 注：`data/docx/v1|v2`（版本对比测试数据）不入库，缺失时 version_diff 的 test_version_compare / test_version_filter 会环境性失败/报错（与代码无关），需从原项目复制该目录后重跑。依赖外部 LLM 端点的测试（如 test_version_filter.py）离线时可能不稳定。Windows 下个别用例因临时目录/超大环境变量触发环境性失败，详见 [开发指南](docs/开发指南.md) 第 2 节。
