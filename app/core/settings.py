"""从环境变量和可选的 .env 文件加载运行配置。"""

from functools import lru_cache
from pathlib import Path
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
    health_document_max_pages: int = Field(default=20, ge=1, le=100)
    health_document_render_dpi: int = Field(default=200, ge=100, le=400)
    health_document_fallback_render_dpi: int = Field(default=300, ge=100, le=400)
    deepseek_api_key: str = ""
    dashscope_api_key: str = ""
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    memory_enabled: bool = True
    memory_top_k: int = Field(default=6, ge=1, le=50)
    memory_score_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    memory_context_max_chars: int = Field(default=2400, ge=200, le=20000)
    memory_collection_prefix: str = "fitagent_memory"
    memory_storage_path: str = "storage/memory"
    memory_llm_model: str = ""
    memory_embedding_model: str = ""
    memory_embedding_dimensions: int = Field(default=1536, ge=1, le=65536)
    memory_timeout_seconds: float = Field(default=10.0, gt=0.0, le=300.0)
    memory_max_retries: int = Field(default=2, ge=1, le=10)
    memory_default_ttl_days: int = Field(default=90, ge=1, le=3650)
    memory_max_list_items: int = Field(default=1000, ge=1, le=100000)
    agent_max_steps: int = Field(default=8, ge=2, le=32)
    agent_max_tool_calls: int = Field(default=6, ge=1, le=16)
    coros_mcp_gateway_url: str = "https://mcp.coros.com/mcp"
    coros_oauth_redirect_uri: str = ""
    coros_oauth_post_connect_redirect_uri: str = "http://localhost:5173/dashboard"
    coros_token_encryption_key: str = ""
    coros_oauth_timeout_seconds: float = Field(default=15.0, gt=0.0, le=60.0)
    coros_mcp_timeout_seconds: float = Field(default=30.0, gt=0.0, le=120.0)
    weatherstack_access_key: str = ""

    @property
    def database_url(self) -> str:
        """返回已处理密码转义的 MySQL SQLAlchemy 连接地址。"""

        return (
            f"mysql+pymysql://{self.mysql_user}:{quote_plus(self.mysql_password)}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def project_root(self) -> Path:
        """返回仓库根目录，供外部 Python 运行器导入 ``app``。"""

        return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回经过校验且已缓存的运行配置。"""

    return Settings()
