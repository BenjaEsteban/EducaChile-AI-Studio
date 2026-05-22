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
    ELEVENLABS_API_KEY: str | None = None
    ELEVENLABS_VOICE_ID: str | None = None
    WAVESPEED_API_KEY: str | None = None
    WAVESPEED_BASE_URL: str = "https://api.wavespeed.ai/api/v3"
    TTS_PROVIDER: str = "none"
    ALLOW_DUMMY_TTS: bool = False
    AVATAR_GENERATION_MODE: str = "image_audio_infinitetalk"
    AVATAR_LIPSYNC_PROVIDER: str = "wavespeed_sync_lipsync_3"
    AVATAR_IMAGE_AUDIO_PROVIDER: str = "wavespeed-ai/infinitetalk-fast"
    AVATAR_IMAGE_AUDIO_RESOLUTION: str = "480p"
    AVATAR_SYNC_MODE: str = "loop"
    AVATAR_LIPSYNC_MODEL_PATH: str = "wavespeed-ai/sync-lipsync-3"
    TTS_LANGUAGE: str = "es"
    TTS_SPEED: float = 0.85
    ENABLE_SUBTITLES: bool = False
    FFMPEG_TIMEOUT_SECONDS: int = 900
    SLIDE_PAUSE_SECONDS: float = 0.5
    AVATAR_LIPSYNC_RESOLUTION: str = "480p"
    AVATAR_BASE_VIDEO_PROVIDER: str = "wavespeed_infinitetalk_fast"
    AVATAR_BASE_VIDEO_DURATION_SECONDS: int = 8
    WAVESPEED_PREDICTION_TIMEOUT_SECONDS: int = 1800
    WAVESPEED_POLL_INTERVAL_SECONDS: int = 8
    WAVESPEED_HTTP_TIMEOUT_SECONDS: int = 300
    MAX_TTS_CHARS_PER_CHUNK: int = 700
    MAX_LIPSYNC_AUDIO_SECONDS_PER_CHUNK: int = 600
    MAX_AVATAR_AUDIO_SECONDS_PER_CHUNK: int = 30
    MAX_CHUNKS_PER_SLIDE: int = 3
    AVATAR_SLIDE_CONCURRENCY: int = 2
    AVATAR_CHUNK_CONCURRENCY: int = 2
    AVATAR_PROVIDER_SLIDE_TIMEOUT_SECONDS: int = 300
    AVATAR_PROVIDER_CHUNK_TIMEOUT_SECONDS: int = 300
    AVATAR_PROVIDER_MAX_RETRIES: int = 1
    ENABLE_STATIC_AVATAR_FALLBACK: bool = True
    FAIL_ON_STATIC_AVATAR_FALLBACK: bool = False
    ALLOW_BASE_VIDEO_AS_FINAL_OVERLAY: bool = False
    ENABLE_AVATAR_CHROMAKEY: bool = False
    AVATAR_CHROMAKEY_COLOR: str = "0x00FF00"
    AVATAR_CHROMAKEY_SIMILARITY: float = 0.15
    AVATAR_CHROMAKEY_BLEND: float = 0.05
    MAX_AUDIO_CHUNK_DURATION_TOLERANCE_SECONDS: float = 1.0
    MIN_EXPECTED_AUDIO_DURATION_RATIO: float = 0.5
    REQUIRE_EXTERNAL_PROVIDER_URL_VALIDATION: bool = True
    GENERATION_STALLED_AFTER_SECONDS: int = 900
    PROVIDER_OPERATION_STALLED_AFTER_SECONDS: int = 1200
    CELERY_TASK_SOFT_TIME_LIMIT: int = 3300
    CELERY_TASK_TIME_LIMIT: int = 3600
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
