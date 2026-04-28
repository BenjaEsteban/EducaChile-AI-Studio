from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_ENV: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "changeme-secret-key-min-32-chars"
    ENCRYPTION_KEY: str = "dev-only-change-me-encryption-key"
    ENABLE_DEV_SEED: bool = True
    CORS_ORIGINS: str = "http://localhost:3000"

    # Database
    DATABASE_URL: str = "postgresql+psycopg://educachile:changeme@localhost:5432/educa_chile"

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "changeme"
    MINIO_BUCKET: str = "educachile"
    MINIO_SECURE: bool = False
    MINIO_PUBLIC_ENDPOINT: str = "localhost:9000"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
