"""数据库连接管理。

两个引擎，用途严格分开：

- ``get_engine()``：**可写**引擎。用于元数据读取、建模取数、预置表初始化脚本。
- ``get_readonly_engine()``：**Agent 专用只读**引擎。除了 SQL 层的只读校验
  （``tools/sql_guard``），还会在每次新建连接时把数据库会话设为只读，作为第二道
  防线：即使校验器被绕过，数据库也会拒绝写入。

会话级只读语句按方言选择；没有对应语句的方言退化为仅依赖 SQL 校验器。
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from core.config import settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_readonly_engine: Engine | None = None

#: 各方言的会话级只读语句
_READONLY_SESSION_SQL = {
    "mysql": "SET SESSION TRANSACTION READ ONLY",
    "mariadb": "SET SESSION TRANSACTION READ ONLY",
    "postgresql": "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
    "sqlite": "PRAGMA query_only = ON",
}


def get_engine() -> Engine:
    """获取全局可写 SQLAlchemy Engine（懒加载）。"""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def _attach_readonly_session(engine: Engine) -> None:
    """在每次新建连接时把会话设为只读（尽力而为，失败只告警）。"""
    statement = _READONLY_SESSION_SQL.get(engine.dialect.name)
    if statement is None:
        logger.debug(
            "方言 %s 无会话级只读语句，仅依赖 SQL 校验器", engine.dialect.name
        )
        return

    @event.listens_for(engine, "connect")
    def _set_readonly_session(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(statement)
        except Exception as exc:  # noqa: BLE001 - 不因缺少权限而阻断查询
            logger.warning("设置只读会话失败（继续依赖 SQL 校验器）：%s", exc)
        finally:
            cursor.close()


def get_readonly_engine() -> Engine:
    """获取 Agent 专用只读 Engine（懒加载）。"""
    global _readonly_engine
    if _readonly_engine is None:
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
        if settings.db_readonly_session:
            _attach_readonly_session(engine)
        _readonly_engine = engine
    return _readonly_engine


def dispose_engine() -> None:
    """释放两个连接池，测试/重启时使用。"""
    global _engine, _readonly_engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
    if _readonly_engine is not None:
        _readonly_engine.dispose()
        _readonly_engine = None
