import os
from functools import lru_cache
from typing import Literal

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.utils.config_handler import get_models_config


def _require_dashscope_api_key() -> None:
    """在真正初始化模型前检查，避免 import阶段静默失败。"""
    if not os.getenv("DASHSCOPE_API_KEY"):
        raise EnvironmentError("缺少环境变量 DASHSCOPE_API_KEY，无法初始化模型。")


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """惰性获取聊天模型，并通过缓存避免重复初始化。"""

    _require_dashscope_api_key()
    return ChatTongyi(
        model=get_models_config()["chat_model_name"],
        max_tokens=4096,
        streaming=True,
    )


@lru_cache(maxsize=1)
def get_embedding_model() -> Embeddings:
    """惰性获取embedding模型，并通过缓存避免重复初始化。"""

    _require_dashscope_api_key()
    return DashScopeEmbeddings(model=get_models_config()["embedding_model_name"])


@lru_cache(maxsize=2)
def get_vl_model(tier: Literal["primary", "fallback"] = "primary") -> BaseChatModel:
    """返回指定质量层级配置的视觉语言模型。"""
    _require_dashscope_api_key()
    config_key = f"vl_{tier}_model_name"
    return ChatTongyi(model=get_models_config()[config_key], streaming=True, max_tokens=4096)
