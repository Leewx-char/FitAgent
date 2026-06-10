import os
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Optional, Union
from langchain_community.embeddings import DashScopeEmbeddings

from app.utils.config_handler import rag_conf
from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self) -> Optional[Union[Embeddings, BaseChatModel]]:
        pass

class ChatModelFactory(BaseModelFactory):
    def generator(self) -> BaseChatModel:
        return ChatTongyi(model=rag_conf["chat_model_name"], max_tokens=4096, streaming=True)

class EmbeddingsFactory(BaseModelFactory):
    def generator(self) -> Embeddings:
        return DashScopeEmbeddings(model=rag_conf["embedding_model_name"])

def _require_dashscope_api_key() -> None:
    """在真正初始化模型前检查，避免 import阶段静默失败。"""
    if not os.getenv("DASHSCOPE_API_KEY"):
        raise EnvironmentError("缺少环境变量 DASHSCOPE_API_KEY，无法初始化模型。")

@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """惰性获取聊天模型，并通过缓存避免重复初始化。"""
    _require_dashscope_api_key()
    return ChatModelFactory().generator()

@lru_cache(maxsize=1)
def get_embedding_model() -> Embeddings:
    """惰性获取embedding模型，并通过缓存避免重复初始化。"""
    _require_dashscope_api_key()
    return EmbeddingsFactory().generator()

@lru_cache(maxsize=1)
def get_vl_model() -> BaseChatModel:
    _require_dashscope_api_key()
    return ChatTongyi(model="qwen-vl-plus", streaming=True, max_tokens=4096)