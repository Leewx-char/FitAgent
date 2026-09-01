import logging
import os

from datetime import datetime

from app.utils.path_tool import get_abs_path

# 日志保存的根目录
LOG_ROOT = get_abs_path("logs")

# 确保日志的目录存在
os.makedirs(LOG_ROOT, exist_ok=True)

# 日志的格式 error info debug
DEFAULT_LOG_FORMAT = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
)


def get_logger(
    name: str = "agent",
    console_level: int = None,
    file_level: int = None,
    log_file=None,
) -> logging.Logger:
    """创建或复用同时输出到控制台和文件的命名日志器。"""
    # Logger 是日志系统的"入口"和"管理者"
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加Handler
    if logger.handlers:
        return logger

    if console_level is None:
        level_name = os.getenv("LOG_LEVEL", "INFO").upper()
        console_level = getattr(logging, level_name, logging.INFO)
    if file_level is None:
        level_name = os.getenv("LOG_LEVEL", "DEBUG").upper()
        file_level = getattr(logging, level_name, logging.DEBUG)

    # 控制台Handler，Handler 是实际的"执行者"，决定日志输出到哪里
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(DEFAULT_LOG_FORMAT)

    logger.addHandler(console_handler)

    # 文件Handler
    if not log_file:
        log_file = os.path.join(LOG_ROOT, f"{name}_{datetime.now().strftime('%Y%m%d')}.log")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(DEFAULT_LOG_FORMAT)

    logger.addHandler(file_handler)

    return logger


# 快捷获取日志器
logger = get_logger()

if __name__ == "__main__":
    logger.info("信息日志")
    logger.error("错误日志")
    logger.warning("警告日志")
    logger.debug("调试日志")
