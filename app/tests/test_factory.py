"""模型工厂的 DashScope 密钥来源测试。"""

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
