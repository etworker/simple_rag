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
| [快速原型部署方案（AWS 中国区）](docs/快速原型部署方案.md) | 单卡 A10g 原型：g5.2xlarge + 托管向量库/数据库/对话历史/用户管理 + 成本预估 |

---

## 模块边界

| 模块 | 职责 | 关键入口 |
|------|------|----------|
| `doc_parser` | 文档解析（PDF / Word）→ 段落 + 表格 + 定位信息 | `parse(filepath, config)`，`Paragraph`/`Table`/`Document` 模型 |
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

```bash
# 1. 安装依赖（至少同步 rag_demo 即可运行 Web）
cd rag_demo && uv sync

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

- 各模块用 `pyproject.toml` 声明依赖。
- `rag_demo/config.json` 是**本地配置，不入库**（已被 `.gitignore` 排除）。新克隆时复制 `config.example.json` 为 `config.json` 再修改。
- LLM / embedding 的默认值集中在 `llm_chat/src/llm_chat/defaults.py` 与 `version_diff/src/version_diff/config.py`，可用环境变量覆盖（详见 [使用手册](docs/使用手册.md) 与 [开发指南](docs/开发指南.md)）。
- `rag_demo` 采用 **profiles + routing** 配置：在 `config.json` 的 `llm_profiles` 定义多模型，在 `llm_routing` 中按用途（qa / pre_review / conflict_detection）路由。

## 提示词（prompts）

- `version_diff/src/version_diff/prompts/` 存放随包发布的提示词模板：
  - `consistency_judge.txt`：跨文档矛盾判定。
  - `version_filter.txt`：版本对比的实质性变更过滤。
- 模板含 `{count}` / `{items}` 占位符，运行时由代码 `.format(...)` 填充；JSON 示例用 `{{`/`}}` 转义。可通过 `judge.prompt_file` / `judge.prompt_template` 覆盖。

---

## 近期迭代（2026-08-12 ~ 08-13）

- **llm_chat**：后端异常挂 `status_code` / `is_network_error` 属性，`retry` 据此判断重试（不再正则解析异常文本）；公共字段初始化上提到 `_init_common`。
- **跨模块去重**：`version_diff.llm_util` 改用新增的 `llm_chat.ask_once_with_config`；`rag_demo` 的 PDF 页数计算统一为 `get_pdf_page_count`（fitz）。
- **错误处理契约**：`ChatSession.ask` 失败改为向上抛出（不再把 `"[错误]..."` 当答案返回），`/api/qa/ask` 将 LLM 不可用映射为 503。
- 详细决策与原理见 [设计文档 §迭代记录](docs/设计文档.md)。

## 测试

各模块独立 `pytest`：

```bash
cd doc_parser   && uv run pytest
cd llm_chat    && uv run pytest
cd version_diff && uv run pytest
cd rag_demo     && uv run pytest          # 排除需 GPU/模型的用例： -k "not gpu and not e2e"
```

> 注：依赖外部 LLM 端点的测试（如 `version_diff/tests/test_version_filter.py`）在离线环境下可能不稳定，建议加 `@pytest.mark.online` 或在无 LLM 时跳过。Windows 下个别用例因临时目录/超大环境变量触发环境性失败（与代码无关），详见 [开发指南](docs/开发指南.md) 第 2 节。
