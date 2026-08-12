# llm-chat

轻量多后端 LLM 多轮对话管理 — 支持 Bedrock Converse / OpenAI 兼容接口。

零外部依赖（仅 `loguru`），使用标准库 `urllib` 发送 HTTP 请求，无需 `boto3` / `openai` SDK。

---

## 功能概览

- **多后端支持**：AWS Bedrock Converse API + OpenAI 兼容 API（含 vLLM / Ollama / Bedrock Mantle Proxy）
- **多轮对话**：`ChatSession` 自动维护对话历史，支持历史截断
- **单次调用**：`ask_once` 无状态一次性调用，适用于分类/判断/摘要
- **Profile 配置**：命名 LLM 配置 + 路由机制，供 `rag_demo` / `version_diff` 等模块复用
- **自动重试**：429 限流 / 5xx 服务端错误 / 网络错误自动重试，指数退避
- **API Key 分级解析**：显式传入 > 环境变量，避免跨后端误用

---

## 依赖

| 依赖 | 用途 |
|------|------|
| `loguru >= 0.7.3` | 日志 |

Python >= 3.10

---

## 安装

```bash
# 在 simple_rag 项目根目录下
uv sync --project llm_chat

# 或单独安装
cd llm_chat
uv sync
```

---

## 快速上手

### 多轮对话

```python
from llm_chat import ChatSession

# Bedrock Converse
session = ChatSession(
    system_prompt="你是文档助手",
    backend="bedrock",
    model="zai.glm-4.7-flash",
    region="us-east-1",
    api_key_env="AWS_BEARER_TOKEN_BEDROCK",
)

# OpenAI 兼容
session = ChatSession(
    system_prompt="你是文档助手",
    backend="openai",
    model="gpt-5.6-luna",
    base_url="https://bedrock-mantle.us-east-1.api.aws/openai/v1",
    api_key_env="OPENAI_API_KEY",
)

answer = session.ask("备份频率是多少？", context="...检索到的参考文本...")
answer = session.ask("那保留周期呢？")  # 自动带上对话历史

session.reset()  # 清空历史
```

### 单次调用

```python
from llm_chat import ask_once

result = ask_once(
    "这两段话是否矛盾？A说每周备份，B说每月备份。",
    system_prompt="你是文档一致性审查专家。",
    model="zai.glm-4.7-flash",
)
```

### Profile 配置

```python
from llm_chat import resolve_llm_profile, resolve_llm_config

# 定义命名 profile + 路由
profiles = {
    "glm": {"provider": "openai", "model": "gpt-5.6-luna", "base_url": "..."},
    "cheap": {"provider": "bedrock", "model": "zai.glm-4.7-flash"},
}
routing = {"version_compare": "glm", "qa": "glm", "draft": "cheap"}

# 按用途解析
cfg = resolve_llm_profile(profiles, routing, "version_compare")
# -> {"provider": "openai", "model": "gpt-5.6-luna", "base_url": "..."}

# 归一化（支持单配置和 profile 引用两种形式）
cfg = resolve_llm_config({"profile": "glm", "llm_profiles": profiles, "routing": routing})
# -> {"provider": "openai", "model": "gpt-5.6-luna", ...}
```

---

## API 参考

### `ChatSession`

```python
class ChatSession:
    def __init__(
        self,
        system_prompt: str = "",
        backend: str = "",        # "bedrock" | "openai"
        model: str = "",
        max_history: int = 0,     # 最大保留对话轮数（默认 20）
        **kwargs,                 # region, base_url, api_key_env, api_key, max_tokens, timeout, ...
    )

    def ask(self, question: str, context: str = "") -> str
    def reset(self) -> None
    def get_history(self) -> list[dict]
```

### `ask_once`

```python
def ask_once(
    prompt: str,
    system_prompt: str = "",
    backend: str = "",
    model: str = "",
    **kwargs,
) -> str
```

### `resolve_llm_profile` / `resolve_llm_config`

```python
def resolve_llm_profile(
    llm_profiles: dict[str, dict] | None,
    routing: dict[str, str] | None,
    use_case: str,
) -> dict

def resolve_llm_config(llm: dict | None) -> dict
```

---

## 默认配置

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `DEFAULT_BACKEND` | `"bedrock"` | 默认后端（可被 `LLM_BACKEND` 环境变量覆盖） |
| `DEFAULT_MODELS["bedrock"]` | `"zai.glm-4.7-flash"` | Bedrock 默认模型 |
| `DEFAULT_MODELS["openai"]` | `"gpt-4o"` | OpenAI 默认模型 |
| `BEDROCK_DEFAULTS["region"]` | `"us-east-1"` | Bedrock 区域 |
| `BEDROCK_DEFAULTS["max_tokens"]` | `2048` | 最大生成 token 数 |
| `BEDROCK_DEFAULTS["max_retries"]` | `3` | 最大重试次数 |
| `OPENAI_DEFAULTS["base_url"]` | `"https://api.openai.com/v1"` | OpenAI 基础 URL |
| `SESSION_DEFAULTS["max_history"]` | `20` | 最大保留对话轮数 |

环境变量：`LLM_BACKEND` / `LLM_MODEL` / `AWS_REGION` / `LLM_BASE_URL` 可覆盖默认值。

---

## 后端对比

| 特性 | Bedrock Converse | OpenAI 兼容 |
|------|-----------------|-------------|
| 后端名 | `"bedrock"` / `"bedrock_converse"` | `"openai"` |
| API 格式 | `messages[].content[].text` | `messages[].content` |
| 系统提示 | 顶层 `system` 字段 | `role: "system"` 消息 |
| 端点 | `/model/{model}/converse` | `/chat/completions` 或 `/responses` |
| 认证 | Bearer Token | Bearer Token |
| 适用 | AWS Bedrock 原生 | OpenAI / vLLM / Ollama / Bedrock Mantle |

---

## 环境变量

```bash
# Bedrock
AWS_BEARER_TOKEN_BEDROCK=...    # Bedrock Bearer Token
AWS_REGION=us-east-1            # AWS 区域

# OpenAI 兼容
OPENAI_API_KEY=...              # API Key
OPENAI_BASE_URL=https://...     # 基础 URL（可指向 Bedrock Mantle Proxy）

# 自建端点
SELF_HOSTED_LLM_API_KEY=...     # 自建 LLM 的 API Key
```

---

## 测试

```bash
cd llm_chat
uv run python -m pytest tests/ -q
```

---

## 设计文档

详细设计请见 [docs/设计文档.md](docs/设计文档.md)。
