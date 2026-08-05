"""
llm_chat 默认配置常量

所有默认值集中在此，方便使用者覆盖。
使用时通过构造函数参数或配置字典覆盖即可。
"""

# 默认后端
DEFAULT_BACKEND = "bedrock"

# 默认模型（按后端分）
DEFAULT_MODELS = {
    "bedrock": "zai.glm-4.7-flash",
    "bedrock_converse": "zai.glm-4.7-flash",
    "openai": "gpt-4o",
}

# Bedrock 后端默认配置
BEDROCK_DEFAULTS = {
    "region": "us-east-1",
    "api_key_env": "AWS_BEARER_TOKEN_BEDROCK",
    "max_tokens": 2048,
    "timeout": 120,
}

# OpenAI 后端默认配置
OPENAI_DEFAULTS = {
    "base_url": "https://api.openai.com/v1",
    "api_key_env": "OPENAI_API_KEY",
    "max_tokens": 2048,
    "timeout": 120,
    "endpoint": "chat",
}

# 会话默认配置
SESSION_DEFAULTS = {
    "max_history": 20,
}
