from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "changeme-secret-key-min-32-chars"
    ENCRYPTION_KEY: str = "dev-only-change-me-encryption-key"
    ENABLE_DEV_SEED: bool = True
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Database
    DATABASE_URL: str = "postgresql+psycopg://educachile:changeme@localhost:5432/educa_chile"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO
    STORAGE_BACKEND: str = "minio"
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_INTERNAL_ENDPOINT: str | None = None
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "changeme"
    MINIO_BUCKET: str = "educachile"
    MINIO_SECURE: bool = False
    MINIO_PUBLIC_ENDPOINT: str = "localhost:9000"
    EXTERNAL_PROVIDER_ASSET_BASE_URL: str | None = None

    # Azure Blob Storage
    AZURE_STORAGE_CONNECTION_STRING: str | None = None
    AZURE_STORAGE_CONTAINER: str = "educachile-assets"
    AZURE_STORAGE_PUBLIC_BASE_URL: str | None = None
    WAVESPEED_API_KEY: str | None = None
    WAVESPEED_BASE_URL: str = "https://api.wavespeed.ai/api/v3"
    DEFAULT_LIPSYNC_MODEL: str = "wavespeed-ai/ltx-2-19b/lipsync"
    FALLBACK_LIPSYNC_MODEL: str | None = None
    LIPSYNC_PROMPT: str = (
        "A front-facing presenter speaking naturally, visible mouth movement, "
        "subtle natural head motion, realistic lip sync, stable face, well-lit portrait."
    )

    # Development-only diagnostics
    DEBUG_AVATAR_SOURCE_URL: str | None = None

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def cors_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
        if not self.is_production:
            for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
                if origin not in origins:
                    origins.append(origin)
        return origins


settings = Settings()
