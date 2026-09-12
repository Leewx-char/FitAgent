from functools import lru_cache
from typing import Literal

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from app.utils.config_handler import get_models_config
from app.core.settings import get_settings

_DEEPSEEK_NON_THINKING_BODY = {"thinking": {"type": "disabled"}}


def _get_dashscope_api_key() -> str:
    """从 Settings 获取 .env 中的 DashScope 密钥，并在缺失时给出明确错误。"""
    api_key = get_settings().dashscope_api_key.strip()
    if not api_key:
        raise EnvironmentError("缺少 .env 配置 DASHSCOPE_API_KEY，无法初始化模型。")
    return api_key


def _get_deepseek_api_key() -> str:
    """从 Settings 获取 .env 中的 DeepSeek 密钥，并在缺失时给出明确错误。"""
    api_key = get_settings().deepseek_api_key.strip()
    if not api_key:
        raise EnvironmentError("缺少 .env 配置 DEEPSEEK_API_KEY，无法初始化聊天模型。")
    return api_key


def create_deepseek_chat_model(
    *,
    model: str,
    streaming: bool,
    max_tokens: int = 4096,
    timeout: float | None = None,
    max_retries: int = 2,
    api_key: str | None = None,
) -> BaseChatModel:
    """创建非思考模式的 DeepSeek 聊天模型，供主对话和 mem0 提取共用。"""
    resolved_api_key = api_key.strip() if api_key is not None else _get_deepseek_api_key()
    if not resolved_api_key:
        raise EnvironmentError("缺少 .env 配置 DEEPSEEK_API_KEY，无法初始化聊天模型。")
    return ChatDeepSeek(
        model=model,
        api_key=resolved_api_key,
        streaming=streaming,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        # thinking mode 不支持 tool_choice，而结构化输出与 Agent 工具都依赖它。
        extra_body=_DEEPSEEK_NON_THINKING_BODY,
        use_responses_api=False,
    )


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """惰性获取 DeepSeek 聊天模型，并通过缓存避免重复初始化。"""

    return create_deepseek_chat_model(
        model=get_models_config()["chat_model_name"],
        max_tokens=4096,
        streaming=True,
    )


@lru_cache(maxsize=1)
def get_embedding_model() -> Embeddings:
    """惰性获取嵌入模型，并通过缓存避免重复初始化。"""

    api_key = _get_dashscope_api_key()
    return DashScopeEmbeddings(
        model=get_models_config()["embedding_model_name"],
        dashscope_api_key=api_key,
    )


@lru_cache(maxsize=1)
def get_reranker():
    """惰性构造 DashScope 二阶段排序器，避免在应用启动时发起网络请求。"""
    from app.services.reranker import DashScopeReranker
    from app.utils.config_handler import get_vector_store_config

    return DashScopeReranker(
        model_name=get_vector_store_config()["reranker_model"],
        api_key=_get_dashscope_api_key(),
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
