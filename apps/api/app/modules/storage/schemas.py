from pydantic import BaseModel, Field, field_validator


class PresignedUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=500)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    expires_in: int = Field(default=3600, ge=60, le=86400)

    @field_validator("filename")
    @classmethod
    def no_path_traversal(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ValueError("filename inválido")
        return v


class PresignedUploadResponse(BaseModel):
    url: str
    key: str
    expires_in: int
    method: str = "PUT"


class PresignedDownloadResponse(BaseModel):
    url: str
    key: str
    expires_in: int
    method: str = "GET"
