"""破坏性重建脚本的确认护栏。"""

from types import SimpleNamespace

import pytest

from scripts import rebuild_database


def test_rebuild_refuses_nonmatching_confirmation_before_connecting(monkeypatch):
    """错误确认值必须在创建 MySQL 连接前被拒绝。"""

    monkeypatch.setattr(
        rebuild_database,
        "get_settings",
        lambda: SimpleNamespace(
            mysql_database="fitagent_test",
            mysql_host="localhost",
            mysql_port=3306,
            mysql_user="root",
            mysql_password="",
        ),
    )
    monkeypatch.setattr(
        rebuild_database.pymysql,
        "connect",
        lambda **_kwargs: pytest.fail("确认不匹配时不应连接 MySQL"),
    )

    with pytest.raises(RuntimeError, match="confirm-database"):
        rebuild_database.rebuild_database("another_database")
