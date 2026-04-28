import uuid
from collections.abc import Generator
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, create_engine, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, MappedColumn, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import settings

# ── Portable types (PostgreSQL ↔ SQLite) ──────────────────────────────────────

class GUID(TypeDecorator):
    """UUID en PostgreSQL, VARCHAR(36) en SQLite."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value: Any, dialect: Dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value) if isinstance(value, uuid.UUID) else str(uuid.UUID(str(value)))

    def process_result_value(self, value: Any, dialect: Dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class JSONType(TypeDecorator):
    """JSONB en PostgreSQL, TEXT (JSON serializado) en SQLite."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        import json
        return json.dumps(value)

    def process_result_value(self, value: Any, dialect: Dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        import json
        return json.loads(value) if isinstance(value, str) else value


# ── Engine & session ──────────────────────────────────────────────────────────

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=settings.DEBUG,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """Agrega created_at / updated_at a cualquier modelo que lo herede."""

    created_at: MappedColumn[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: MappedColumn[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
