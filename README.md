# EducaChile AI Studio

Plataforma SaaS de inteligencia artificial para la creación, gestión y distribución de contenido educativo personalizado para el mercado chileno.

## Stack

| Capa | Tecnología |
|------|-----------|
| Frontend | Next.js 14 + React + TypeScript + Tailwind CSS |
| Backend | FastAPI + Python 3.12 |
| Base de datos | PostgreSQL 16 |
| Cola de tareas | Redis + Celery |
| Almacenamiento | MinIO |
| Parsing PPTX | python-pptx |
| Infra local | Docker Compose |

## Estructura

```
EducaChile-AI-Studio/
├── apps/
│   ├── web/          # Next.js frontend
│   └── api/          # FastAPI backend + Celery workers
├── packages/
│   ├── shared-types/ # Tipos TypeScript compartidos
│   └── ui/           # Componentes React reutilizables
├── infra/            # Configuraciones de infraestructura
├── docs/             # Documentación técnica
├── scripts/          # Scripts de utilidad
└── .github/          # CI/CD workflows
```

## Inicio rápido

### 1. Variables de entorno

```bash
cp .env.example .env
```

> Edita `.env` si necesitas cambiar credenciales. Los valores por defecto funcionan para desarrollo local.

### 2. Levantar todos los servicios

```bash
docker compose up --build
```

La primera vez descarga imágenes e instala dependencias (~3-5 min). Las siguientes veces es instantáneo.

### 3. Ejecutar migraciones y seed local

En otra terminal:

```bash
make migrate
make seed-dev
```

`make seed-dev` crea datos determinísticos solo para desarrollo local:

| Registro | ID / valor |
|----------|------------|
| Organización | `00000000-0000-0000-0000-000000000001` |
| Usuario | `00000000-0000-0000-0000-000000000002` |
| Email usuario | `dev@educachile.local` |

El backend usa temporalmente esos IDs como identidad mock hasta que exista autenticación real.
El seed es idempotente y también intenta ejecutarse al iniciar la API cuando `APP_ENV=development`
y `ENABLE_DEV_SEED=true`. Si las tablas aún no existen, se omite y debes ejecutarlo después de
las migraciones con `make seed-dev`.

### 4. Verificar que todo está corriendo

```bash
docker compose ps
```

| Servicio | URL |
|----------|-----|
| Frontend (Next.js) | http://localhost:3000 |
| Backend (FastAPI) | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| MinIO Console | http://localhost:9001 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

### 5. Verificar conexión backend

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### 6. Ejecutar tests

```bash
make test
```

## Migraciones de base de datos

El proyecto usa **Alembic** para gestionar el esquema de PostgreSQL.

```bash
# Aplicar todas las migraciones pendientes
make migrate

# Revertir la última migración
make migrate-down

# Ver historial de migraciones
make migrate-history

# Crear una nueva migración (autogenerate detecta cambios en los modelos)
make migrate-new name=nombre_descriptivo
```

> Las migraciones viven en `apps/api/alembic/versions/`. Cuando agregues un modelo nuevo,
> impórtalo en `apps/api/alembic/env.py` y ejecuta `make migrate-new name=...`.

## Comandos disponibles

```bash
make up       # Levantar todos los servicios (docker compose up -d)
make down     # Detener servicios
make build    # Reconstruir imágenes
make logs     # Ver logs en tiempo real
make migrate  # Aplicar migraciones pendientes
make seed-dev # Crear usuario/organización mock para desarrollo local
make test     # Ejecutar tests
make lint     # Ejecutar linters
make shell-api  # Shell dentro del contenedor api
make shell-db   # psql dentro del contenedor db
```

## Desarrollo

Los servicios `api` y `web` tienen **hot reload** activado:
- Cambios en `apps/api/app/` se reflejan automáticamente en el contenedor `api` y `worker`
- Cambios en `apps/web/src/` se reflejan automáticamente en el contenedor `web`

No es necesario reconstruir la imagen para cambios de código. Solo usa `--build` cuando modifiques `pyproject.toml`, `package.json` o los `Dockerfile`.

## Preview público local

Para mostrar el MVP local con una URL pública temporal, usa ngrok o Cloudflare Tunnel:

```bash
scripts/expose-local.sh
```

Luego copia las URLs públicas a `.env`:

```bash
NEXT_PUBLIC_API_URL=https://<backend-tunnel>
CORS_ORIGINS=http://localhost:3000,https://<frontend-tunnel>
```

Reinicia los servicios que leen esas variables:

```bash
docker compose up -d --force-recreate api web
```

Si personas fuera de tu máquina deben subir PPT/PPTX, también debes exponer MinIO y ajustar
`MINIO_PUBLIC_ENDPOINT`. Ver [docs/local-public-preview.md](docs/local-public-preview.md).

## Deploy producción

El deploy de producción usa GitHub Actions, Docker Hub privado y un servidor Linux por SSH.
Ver [docs/production-deployment.md](docs/production-deployment.md).

## Servicios y puertos

```
┌─────────┬──────────────────────────────┬────────────────────────────────┐
│ Servicio│ Puerto                       │ Notas                          │
├─────────┼──────────────────────────────┼────────────────────────────────┤
│ web     │ 3000                         │ Next.js dev server             │
│ api     │ 8000                         │ FastAPI + Swagger en /docs     │
│ db      │ 5432                         │ PostgreSQL, DB: educa_chile    │
│ redis   │ 6379                         │ Broker para Celery             │
│ minio   │ 9000 (API) / 9001 (console)  │ S3-compatible storage          │
│ worker  │ —                            │ Celery worker (sin puerto)     │
└─────────┴──────────────────────────────┴────────────────────────────────┘
```
