#!/bin/bash
set -e

echo "→ Copiando variables de entorno..."
[ ! -f .env ] && cp .env.example .env || echo "  .env ya existe, omitiendo."

echo "→ Levantando servicios..."
docker compose up -d

echo "→ Esperando base de datos..."
sleep 5

echo "→ Ejecutando migraciones..."
docker compose exec api alembic upgrade head

echo "✓ Setup completo. Visita http://localhost:3000"
