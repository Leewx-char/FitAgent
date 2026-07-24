from functools import lru_cache
from typing import Literal

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.utils.config_handler import get_models_config
from app.core.settings import get_settings


def _get_dashscope_api_key() -> str:
    """从 Settings 获取 .env 中的 DashScope 密钥，并在缺失时给出明确错误。"""
    api_key = get_settings().dashscope_api_key.strip()
    if not api_key:
        raise EnvironmentError("缺少 .env 配置 DASHSCOPE_API_KEY，无法初始化模型。")
    return api_key


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """惰性获取聊天模型，并通过缓存避免重复初始化。"""

    api_key = _get_dashscope_api_key()
    return ChatTongyi(
        model=get_models_config()["chat_model_name"],
        max_tokens=4096,
        streaming=True,
        dashscope_api_key=api_key,
    )


@lru_cache(maxsize=1)
def get_embedding_model() -> Embeddings:
    """惰性获取embedding模型，并通过缓存避免重复初始化。"""

    api_key = _get_dashscope_api_key()
    return DashScopeEmbeddings(
        model=get_models_config()["embedding_model_name"],
        dashscope_api_key=api_key,
    )


@lru_cache(maxsize=2)
def get_vl_model(tier: Literal["primary", "fallback"] = "primary") -> BaseChatModel:
    """返回指定质量层级配置的视觉语言模型。"""
    api_key = _get_dashscope_api_key()
    config_key = f"vl_{tier}_model_name"
    return ChatTongyi(
        model=get_models_config()[config_key],
        streaming=True,
        max_tokens=4096,
        dashscope_api_key=api_key,
    )
