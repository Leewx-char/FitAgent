"""从环境变量和可选的 .env 文件加载运行配置。"""

from functools import lru_cache
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

    @property
    def database_url(self) -> str:
        """返回已处理密码转义的 MySQL SQLAlchemy 连接地址。"""

        return (
            f"mysql+pymysql://{self.mysql_user}:{quote_plus(self.mysql_password)}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回经过校验且已缓存的运行配置。"""

    return Settings()
