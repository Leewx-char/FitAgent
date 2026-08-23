"""从环境变量和可选的 .env 文件加载运行配置。"""

import json
import shlex
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """不得硬编码在业务逻辑中的运行配置。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_database: str = "zhitong"
    auto_create_database: bool = False
    health_document_max_pages: int = Field(default=20, ge=1, le=100)
    health_document_render_dpi: int = Field(default=200, ge=100, le=400)
    health_document_fallback_render_dpi: int = Field(default=300, ge=100, le=400)
    dashscope_api_key: str = ""
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    agent_max_steps: int = Field(default=8, ge=2, le=32)
    agent_max_tool_calls: int = Field(default=6, ge=1, le=16)
    coros_mcp_command: str = "coros-mcp serve"
    coros_mcp_sync_command: str = "coros-mcp sync"
    coros_mcp_cache_home: str = ".tools/coros-mcp-home"
    # FitAgent only consumes device data. Do not let an application setting widen the MCP
    # server to include write tools, even though the community server supports them.
    coros_mcp_toolset: Literal["readonly"] = "readonly"
    coros_mcp_hide_auth_tools: bool = True

    @property
    def database_url(self) -> str:
        """返回已处理密码转义的 MySQL SQLAlchemy 连接地址。"""

        return (
            f"mysql+pymysql://{self.mysql_user}:{quote_plus(self.mysql_password)}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def coros_mcp_command_parts(self) -> tuple[str, ...]:
        """Parse a command string or a JSON argv array without invoking a shell."""

        return self._parse_command(self.coros_mcp_command, "COROS_MCP_COMMAND")

    @property
    def coros_mcp_sync_command_parts(self) -> tuple[str, ...]:
        """Return the one-shot cache synchronization command without invoking a shell."""

        return self._parse_command(self.coros_mcp_sync_command, "COROS_MCP_SYNC_COMMAND")

    @staticmethod
    def _parse_command(value: str, setting_name: str) -> tuple[str, ...]:
        value = value.strip()
        if not value:
            raise ValueError(f"{setting_name} 不能为空")
        if value.startswith("["):
            parsed = json.loads(value)
            if (
                not isinstance(parsed, list)
                or not parsed
                or not all(isinstance(item, str) and item for item in parsed)
            ):
                raise ValueError(f"{setting_name} JSON 必须是非空字符串数组")
            return tuple(parsed)
        return tuple(shlex.split(value, posix=False))

    @property
    def coros_mcp_cache_home_path(self) -> Path:
        """Return an absolute private home directory for the external MCP cache."""

        configured = Path(self.coros_mcp_cache_home).expanduser()
        return configured if configured.is_absolute() else (Path.cwd() / configured).resolve()

    @property
    def project_root(self) -> Path:
        """Return the repository root so the external Python runner can import ``app``."""

        return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回经过校验且已缓存的运行配置。"""

    return Settings()
