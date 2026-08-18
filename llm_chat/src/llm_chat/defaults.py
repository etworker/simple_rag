"""
llm_chat 默认配置常量

所有默认值集中在此，方便使用者覆盖。
使用时通过构造函数参数或配置字典覆盖即可。
"""

import os

# 环境变量名（集中常量，避免裸字面量散落多处）
ENV_LLM_BACKEND = "LLM_BACKEND"
ENV_LLM_MODEL = "LLM_MODEL"
ENV_AWS_REGION = "AWS_REGION"
BEDROCK_API_KEY_ENV = "AWS_BEARER_TOKEN_BEDROCK"  # Bedrock Bearer Token 所在环境变量

# 默认模型（按后端分）
DEFAULT_LLM_MODEL = "zai.glm-4.7-flash"  # Bedrock 默认

# 默认后端
DEFAULT_BACKEND = os.environ.get(ENV_LLM_BACKEND, "bedrock")

# 默认模型（按后端分）
DEFAULT_MODELS = {
    "bedrock": os.environ.get(ENV_LLM_MODEL, DEFAULT_LLM_MODEL),
    "bedrock_converse": os.environ.get(ENV_LLM_MODEL, DEFAULT_LLM_MODEL),
    "openai": os.environ.get(ENV_LLM_MODEL, "gpt-4o"),
}

# Bedrock 后端默认配置
BEDROCK_DEFAULTS = {
    "region": os.environ.get(ENV_AWS_REGION, "us-east-1"),
    "api_key_env": BEDROCK_API_KEY_ENV,
    "max_tokens": 2048,
    "timeout": 120,
    "max_retries": 3,
    "retry_backoff": 2.0,
    "context_window": 8192,
    "concurrency": 1,
}

# OpenAI 后端默认配置
OPENAI_DEFAULTS = {
    "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
    "api_key_env": "OPENAI_API_KEY",
    "max_tokens": 2048,
    "timeout": 120,
    "endpoint": "chat",
    "max_retries": 3,
    "retry_backoff": 2.0,
}

# 会话默认配置
SESSION_DEFAULTS = {
    "max_history": 20,
}
