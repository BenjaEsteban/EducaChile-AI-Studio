from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Alembic Config — acceso a alembic.ini
config = context.config

# Logging desde alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Importar Base y settings DESPUÉS de configurar logging
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

# Modelos — deben importarse para que Alembic los detecte en autogenerate
from app.modules.users.models import User  # noqa: F401
from app.modules.organizations.models import Organization, OrganizationMember  # noqa: F401
from app.modules.projects.models import (  # noqa: F401
    Asset,
    GenerationConfig,
    Presentation,
    Project,
    Slide,
)
from app.modules.jobs.models import Job  # noqa: F401

# Inyectar DATABASE_URL desde pydantic-settings (ignora el valor en alembic.ini)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
