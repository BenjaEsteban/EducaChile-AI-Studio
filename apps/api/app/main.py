from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.APP_ENV == "development" and settings.ENABLE_DEV_SEED:
        from app.dev_seed import run_dev_seed

        run_dev_seed()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="EducaChile AI Studio API",
        version="0.1.0",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.modules.health.router import router as health_router
    from app.modules.jobs.router import router as jobs_router
    from app.modules.presentations.router import router as presentations_router
    from app.modules.projects.router import router as projects_router
    from app.modules.slides.router import router as slides_router
    from app.modules.storage.router import router as storage_router

    app.include_router(health_router)
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(storage_router, prefix="/api/v1")
    app.include_router(presentations_router, prefix="/api/v1")
    app.include_router(slides_router, prefix="/api/v1")

    return app


app = create_app()
