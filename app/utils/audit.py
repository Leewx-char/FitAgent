"""审计日志 —— 记录"谁在什么时候做了什么关键操作"，用于安全追溯。

与普通业务日志（logger）的区别：
  - 独立文件 logs/audit_YYYYMMDD.log，append-only，便于归档、不与业务日志混杂
  - 结构化 JSON 行，每条含 request_id/user_id/action/result，方便机器解析和检索
  - 不进业务 DB 事务：即使请求失败回滚，审计记录依然落盘（登录失败等恰恰要记）

用法：在关键操作处调用
  audit_log("login", user_id=user.id, result="success")
  audit_log("login", result="fail", username=name)   # 失败无 user_id
"""

import json
import logging
import os
from datetime import datetime

from app.utils.path_tool import get_abs_path
from app.core.request_context import request_id_var

LOG_ROOT = get_abs_path("logs")
os.makedirs(LOG_ROOT, exist_ok=True)


def _build_audit_logger() -> logging.Logger:
    """创建或复用不向根日志器传播的审计日志器。"""
    audit_logger = logging.getLogger("audit")
    audit_logger.setLevel(logging.INFO)
    # 不向 root 传播，避免审计条目又被业务 logger 打一遍
    audit_logger.propagate = False
    if audit_logger.handlers:
        return audit_logger

    log_file = os.path.join(LOG_ROOT, f"audit_{datetime.now().strftime('%Y%m%d')}.log")
    handler = logging.FileHandler(log_file, encoding="utf-8")
    # 审计只存 message 本体（已是 JSON），不加业务日志那套前缀
    handler.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(handler)
    return audit_logger


_audit_logger = _build_audit_logger()


def audit_log(action: str, user_id: int | None = None, result: str = "success", **detail) -> None:
    """写一条审计记录。action 为操作类型，result 为 success/fail，
    detail 里放操作相关的补充字段（如失败登录的 username）。"""
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "request_id": request_id_var.get(),
        "user_id": user_id,
        "action": action,
        "result": result,
    }
    if detail:
        entry.update(detail)
    _audit_logger.info(json.dumps(entry, ensure_ascii=False))
