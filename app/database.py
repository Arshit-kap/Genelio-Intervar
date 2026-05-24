"""
SQLAlchemy Database Setup
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import SQLALCHEMY_DATABASE_URL

def _get_connect_args():
    if "sqlite" in SQLALCHEMY_DATABASE_URL:
        # timeout=60 seconds wait for write locks during ingestion
        return {"check_same_thread": False, "timeout": 60}
    return {}

def _on_connect(dbapi_con, _):
    """Enable WAL mode on every new SQLite connection for concurrent read/write."""
    if hasattr(dbapi_con, "execute"):
        try:
            dbapi_con.execute("PRAGMA journal_mode=WAL")
            dbapi_con.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass

from sqlalchemy import event

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args=_get_connect_args(),
)

if "sqlite" in SQLALCHEMY_DATABASE_URL:
    event.listen(engine, "connect", _on_connect)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependency for getting DB session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
