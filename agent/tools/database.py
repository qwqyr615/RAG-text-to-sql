"""数据库连接管理。

当前使用 SQLAlchemy 统一管理连接。
后续接入 MySQL / PostgreSQL 时只需要修改 DATABASE_URL，不需要改业务代码。
"""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from core.config import settings

_engine: Engine | None = None


def get_engine() -> Engine:
    """获取全局 SQLAlchemy Engine（懒加载）。"""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def dispose_engine() -> None:
    """释放连接池，测试/重启时使用。"""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
