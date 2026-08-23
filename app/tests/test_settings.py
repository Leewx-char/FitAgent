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


def test_settings_parses_coros_command_as_json_argv(monkeypatch, tmp_path):
    monkeypatch.delenv("COROS_MCP_COMMAND", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        'COROS_MCP_COMMAND=["C:\\\\Program Files\\\\Coros MCP\\\\coros-mcp.exe", "serve"]\n',
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.coros_mcp_command_parts == (
        r"C:\Program Files\Coros MCP\coros-mcp.exe",
        "serve",
    )


def test_settings_keeps_coros_mcp_readonly(monkeypatch, tmp_path):
    monkeypatch.delenv("COROS_MCP_TOOLSET", raising=False)
    monkeypatch.delenv("COROS_MCP_HIDE_AUTH_TOOLS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("COROS_MCP_HIDE_AUTH_TOOLS=false\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.coros_mcp_toolset == "readonly"
    assert settings.coros_mcp_hide_auth_tools is False


def test_settings_resolves_relative_coros_cache_home(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COROS_MCP_CACHE_HOME", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("COROS_MCP_CACHE_HOME=.tools/coros-cache\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.coros_mcp_cache_home_path == tmp_path / ".tools" / "coros-cache"
