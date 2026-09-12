"""运行配置的 .env 读取测试。"""

from app.core.settings import Settings


def test_settings_reads_dashscope_api_key_from_env_file(monkeypatch, tmp_path):
    """验证设置对象可从指定 .env 文件读取两个模型提供商的密钥。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=deepseek-key-from-dotenv\nDASHSCOPE_API_KEY=dashscope-key-from-dotenv\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.deepseek_api_key == "deepseek-key-from-dotenv"
    assert settings.dashscope_api_key == "dashscope-key-from-dotenv"


def test_settings_reads_agent_execution_budgets_from_env_file(monkeypatch, tmp_path):
    """验证设置对象从 .env 读取 Agent 步数和工具调用预算。"""
    monkeypatch.delenv("AGENT_MAX_STEPS", raising=False)
    monkeypatch.delenv("AGENT_MAX_TOOL_CALLS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_MAX_STEPS=10\nAGENT_MAX_TOOL_CALLS=4\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.agent_max_steps == 10
    assert settings.agent_max_tool_calls == 4


def test_settings_reads_official_coros_oauth_fields(monkeypatch, tmp_path):
    """验证官方远程 MCP 与 OAuth 配置可由环境文件加载。"""
    monkeypatch.delenv("COROS_MCP_GATEWAY_URL", raising=False)
    monkeypatch.delenv("COROS_OAUTH_REDIRECT_URI", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COROS_MCP_GATEWAY_URL=https://mcp.coros.com/mcp\n"
        "COROS_OAUTH_REDIRECT_URI=https://api.example.com/api/coros/callback\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.coros_mcp_gateway_url == "https://mcp.coros.com/mcp"
    assert settings.coros_oauth_redirect_uri.endswith("/api/coros/callback")


def test_settings_uses_safe_coros_gateway_default(monkeypatch, tmp_path):
    """未显式指定时仍使用官方 gateway，而不是社区本地命令。"""
    monkeypatch.delenv("COROS_MCP_GATEWAY_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.coros_mcp_gateway_url == "https://mcp.coros.com/mcp"
