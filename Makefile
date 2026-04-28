.PHONY: up down build logs \
        migrate migrate-down migrate-history migrate-new \
        seed-dev test lint \
        shell-api shell-db

# ── Docker ────────────────────────────────────────────────────────────────────

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

# ── Migraciones (Alembic) ─────────────────────────────────────────────────────

migrate:
	docker compose exec api alembic upgrade head

migrate-down:
	docker compose exec api alembic downgrade -1

migrate-history:
	docker compose exec api alembic history --verbose

migrate-new:
	@test -n "$(name)" || (echo "Uso: make migrate-new name=descripcion" && exit 1)
	docker compose exec api alembic revision --autogenerate --message "$(name)"

seed-dev:
	docker compose exec api python -m app.dev_seed

# ── Tests y linters ───────────────────────────────────────────────────────────

test:
	docker compose exec api pytest

lint:
	docker compose exec api ruff check app/
	docker compose exec web npm run lint

# ── Shells ────────────────────────────────────────────────────────────────────

shell-api:
	docker compose exec api bash

shell-db:
	docker compose exec db psql -U $${POSTGRES_USER} $${POSTGRES_DB}
