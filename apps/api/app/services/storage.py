from typing import BinaryIO, Protocol


class StorageService(Protocol):
    def upload_bytes(
        self,
        object_key: str,
        data: bytes,
        content_type: str,
        overwrite: bool = True,
    ) -> str: ...

    def upload_stream(
        self,
        object_key: str,
        stream: BinaryIO,
        content_type: str,
        overwrite: bool = True,
    ) -> str: ...

    def download_bytes(self, object_key: str) -> bytes: ...

    def delete(self, object_key: str) -> None: ...

    def generate_read_url(self, object_key: str, expires_minutes: int = 60) -> str: ...

    def exists(self, object_key: str) -> bool: ...
