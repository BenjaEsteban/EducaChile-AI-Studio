import uuid

from fastapi import HTTPException, status

from app.providers.storage.base import PresignedURL, StorageProvider, UploadedFile
from app.modules.storage.schemas import PresignedUploadRequest


class StorageService:
    def __init__(self, provider: StorageProvider) -> None:
        self._provider = provider

    # ── Key helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def build_key(org_id: uuid.UUID, project_id: uuid.UUID, filename: str) -> str:
        """Estructura: {org_id}/{project_id}/{uuid}_{filename}
        El prefijo UUID evita colisiones si se sube el mismo nombre dos veces.
        """
        unique = uuid.uuid4().hex[:8]
        return f"{org_id}/{project_id}/{unique}_{filename}"

    @staticmethod
    def build_asset_key(org_id: uuid.UUID, asset_type: str, filename: str) -> str:
        """Para assets sin project_id conocido: {org_id}/{asset_type}/{uuid}_{filename}"""
        unique = uuid.uuid4().hex[:8]
        return f"{org_id}/{asset_type}/{unique}_{filename}"

    # ── Operations ────────────────────────────────────────────────────────────

    def upload(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> UploadedFile:
        return self._provider.upload_file(key, data, content_type)

    def download(self, key: str) -> bytes:
        try:
            return self._provider.download_file(key)
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {key}",
            )

    def presigned_upload(
        self,
        request: PresignedUploadRequest,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> PresignedURL:
        key = self.build_key(org_id, project_id, request.filename)
        return self._provider.generate_presigned_upload_url(
            key=key,
            content_type=request.content_type,
            expires_in=request.expires_in,
        )

    def presigned_download(self, key: str, expires_in: int = 3600) -> PresignedURL:
        try:
            return self._provider.generate_presigned_download_url(
                key=key, expires_in=expires_in
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {key}",
            )

    def delete(self, key: str) -> None:
        try:
            self._provider.delete_file(key)
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {key}",
            )
