"""数据库基础设施：引擎、会话、Base 与初始化。

约定（阶段三）：API 路由禁止直接写 SQL，一律经 `app/database/repository.py`。
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_DIR, DATABASE_URL, ensure_dirs


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


def create_db_engine(url: str = DATABASE_URL):
    """创建 SQLite 引擎（统一打开外键 PRAGMA），测试可传入临时库 URL 复用。"""
    db_engine = create_engine(
        url,
        connect_args={"check_same_thread": False},  # SQLite 默认禁止跨线程，FastAPI 多线程需要放开
        future=True,
    )

    @event.listens_for(db_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
        """SQLite 默认不启用外键约束，连接时打开 PRAGMA。"""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return db_engine


engine = create_db_engine()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表（幂等）：确保数据目录存在并 create_all。"""
    from app.database import models  # noqa: F401  导入模型以注册到 Base.metadata

    ensure_dirs()
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def get_db():
    """FastAPI 依赖：每请求一个 Session，请求结束关闭。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
