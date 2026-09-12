"""模型工厂的 DeepSeek 与 DashScope 提供商边界测试。"""

from types import SimpleNamespace

from app.services import factory


def test_factory_reads_dashscope_key_from_settings(monkeypatch):
    """验证模型工厂从运行配置读取 DashScope 密钥。"""
    monkeypatch.setattr(
        factory,
        "get_settings",
        lambda: SimpleNamespace(dashscope_api_key="key-from-dotenv"),
    )

    assert factory._get_dashscope_api_key() == "key-from-dotenv"


def test_factory_reads_deepseek_key_from_settings(monkeypatch):
    """聊天模型必须从运行配置读取 DeepSeek 官方密钥。"""
    monkeypatch.setattr(
        factory,
        "get_settings",
        lambda: SimpleNamespace(deepseek_api_key="deepseek-key"),
    )

    assert factory._get_deepseek_api_key() == "deepseek-key"


def test_factory_builds_cached_deepseek_flash_chat_model(monkeypatch):
    """主对话模型应使用 DeepSeek 官方组件与当前模型名。"""
    monkeypatch.setattr(
        factory,
        "get_settings",
        lambda: SimpleNamespace(deepseek_api_key="deepseek-key"),
    )
    monkeypatch.setattr(factory, "get_models_config", lambda: {"chat_model_name": "deepseek-flash"})
    factory.get_chat_model.cache_clear()

    try:
        model = factory.get_chat_model()
    finally:
        factory.get_chat_model.cache_clear()

    assert model.model_name == "deepseek-flash"
    assert model.streaming is True
    assert model.max_tokens == 4096
    assert model.extra_body == {"thinking": {"type": "disabled"}}
    assert model.use_responses_api is False


def test_factory_keeps_embedding_and_vision_on_dashscope(monkeypatch):
    """迁移聊天模型时，嵌入和视觉模型仍使用 DashScope 适配器。"""
    created_embeddings = []
    created_vision_models = []

    class FakeEmbeddings:
        def __init__(self, **kwargs):
            created_embeddings.append(kwargs)

    class FakeVisionModel:
        def __init__(self, **kwargs):
            created_vision_models.append(kwargs)

    monkeypatch.setattr(
        factory,
        "get_settings",
        lambda: SimpleNamespace(dashscope_api_key="dashscope-key"),
    )
    monkeypatch.setattr(
        factory,
        "get_models_config",
        lambda: {
            "embedding_model_name": "text-embedding-v1",
            "vl_primary_model_name": "qwen-vl-plus",
        },
    )
    monkeypatch.setattr(factory, "DashScopeEmbeddings", FakeEmbeddings)
    monkeypatch.setattr(factory, "ChatTongyi", FakeVisionModel)
    factory.get_embedding_model.cache_clear()
    factory.get_vl_model.cache_clear()

    try:
        factory.get_embedding_model()
        factory.get_vl_model()
    finally:
        factory.get_embedding_model.cache_clear()
        factory.get_vl_model.cache_clear()

    assert created_embeddings == [
        {"model": "text-embedding-v1", "dashscope_api_key": "dashscope-key"}
    ]
    assert created_vision_models == [
        {
            "model": "qwen-vl-plus",
            "streaming": True,
            "max_tokens": 4096,
            "dashscope_api_key": "dashscope-key",
        }
    ]
