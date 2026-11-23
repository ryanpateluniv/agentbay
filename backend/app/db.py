from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    if ":memory:" not in url:
        kwargs |= {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow,
                   "pool_timeout": settings.db_pool_timeout, "pool_pre_ping": True}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return eng


if settings.database_url.startswith("sqlite:///"):
    from pathlib import Path
    Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None):
    from . import models  # noqa: F401
    Base.metadata.create_all(eng or engine)


def release(db) -> None:
    """End the current transaction so its pooled connection goes back to the pool. Call before awaiting an agent,
    an LLM or anything slow: a Session only holds a connection while a transaction is open."""
    if db.in_transaction():
        db.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
