import io
import logging

from minio import Minio
from minio.error import S3Error

from app.config import settings
from app.providers.storage.base import PresignedURL, StorageProvider, UploadedFile

logger = logging.getLogger(__name__)


class MinIOStorageProvider(StorageProvider):
    def __init__(self) -> None:
        self._bucket = settings.MINIO_BUCKET
        self._client = Minio(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self._presign_client = Minio(
            endpoint=settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
            region="us-east-1",
        )
        self._ensure_bucket()

    # ── Bucket setup ──────────────────────────────────────────────────────────

    def _ensure_bucket(self) -> None:
        """Crea el bucket si no existe. Sin política pública."""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("Bucket '%s' creado.", self._bucket)
            else:
                logger.debug("Bucket '%s' ya existe.", self._bucket)
        except S3Error as exc:
            logger.error("Error al verificar/crear bucket '%s': %s", self._bucket, exc)
            raise

    # ── StorageProvider interface ─────────────────────────────────────────────

    def upload_file(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> UploadedFile:
        size = len(data)
        self._client.put_object(
            bucket_name=self._bucket,
            object_name=key,
            data=io.BytesIO(data),
            length=size,
            content_type=content_type,
        )
        logger.debug("Uploaded '%s' (%d bytes)", key, size)
        return UploadedFile(
            key=key,
            bucket=self._bucket,
            size_bytes=size,
            content_type=content_type,
        )

    def download_file(self, key: str) -> bytes:
        try:
            response = self._client.get_object(self._bucket, key)
            return response.read()
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                raise FileNotFoundError(f"Object not found: {key}") from exc
            raise
        finally:
            # get_object devuelve un HTTPResponse — siempre cerrar
            try:
                response.close()  # type: ignore[union-attr]
                response.release_conn()  # type: ignore[union-attr]
            except Exception:
                pass

    def generate_presigned_upload_url(
        self,
        key: str,
        content_type: str = "application/octet-stream",
        expires_in: int = 3600,
    ) -> PresignedURL:
        from datetime import timedelta

        url = self._presign_client.presigned_put_object(
            bucket_name=self._bucket,
            object_name=key,
            expires=timedelta(seconds=expires_in),
        )
        return PresignedURL(url=url, key=key, expires_in=expires_in)

    def generate_presigned_download_url(
        self,
        key: str,
        expires_in: int = 3600,
    ) -> PresignedURL:
        from datetime import timedelta

        url = self._presign_client.presigned_get_object(
            bucket_name=self._bucket,
            object_name=key,
            expires=timedelta(seconds=expires_in),
        )
        return PresignedURL(url=url, key=key, expires_in=expires_in)

    def delete_file(self, key: str) -> None:
        try:
            self._client.remove_object(self._bucket, key)
            logger.debug("Deleted '%s'", key)
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                raise FileNotFoundError(f"Object not found: {key}") from exc
            raise
