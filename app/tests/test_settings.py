"""运行配置的 .env 读取测试。"""

from app.core.settings import Settings


def test_settings_reads_dashscope_api_key_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DASHSCOPE_API_KEY=key-from-dotenv\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.dashscope_api_key == "key-from-dotenv"


def test_settings_reads_agent_execution_budgets_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_MAX_STEPS=10\nAGENT_MAX_TOOL_CALLS=4\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.agent_max_steps == 10
    assert settings.agent_max_tool_calls == 4
