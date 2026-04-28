from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Engine dedicado para el worker — pool pequeño, sin echo
_worker_engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=3,
    echo=False,
)

_WorkerSession = sessionmaker(autocommit=False, autoflush=False, bind=_worker_engine)


@contextmanager
def worker_db_session() -> Generator[Session, None, None]:
    """Context manager que provee una sesión DB para tasks Celery.

    Uso:
        with worker_db_session() as db:
            repo = JobRepository(db)
            ...
    """
    db = _WorkerSession()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
