import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.config import settings

settings.ENABLE_DEV_SEED = False

# SQLite en memoria — sin necesidad de PostgreSQL
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def _override_get_db():
    db = _TestingSession()
    try:
        yield db
    finally:
        db.close()


# Exportado para reutilizar en fixtures de otros test files
override_get_db = _override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    """Recrea todas las tablas antes de cada test para garantizar aislamiento."""
    # Importar modelos para que Base los conozca
    import app.modules.jobs.models  # noqa: F401
    import app.modules.organizations.models  # noqa: F401
    import app.modules.projects.models  # noqa: F401
    import app.modules.users.models  # noqa: F401

    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
