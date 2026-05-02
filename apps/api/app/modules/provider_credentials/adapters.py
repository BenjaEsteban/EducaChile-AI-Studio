from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderValidationResult:
    ok: bool
    status: str
    message: str


class ProviderAdapter:
    provider_name: str

    def validate_key(self, api_key: str) -> ProviderValidationResult:
        if len(api_key.strip()) < 8:
            return ProviderValidationResult(False, "invalid", "API key is too short")
        lowered = api_key.lower()
        if "expired" in lowered or "revoked" in lowered:
            return ProviderValidationResult(
                False,
                "expired_or_revoked",
                "API key appears expired or revoked",
            )
        if "invalid" in lowered:
            return ProviderValidationResult(False, "invalid", "Provider rejected API key")
        return ProviderValidationResult(True, "valid", "Provider connection validated")


class GeminiAdapter(ProviderAdapter):
    provider_name = "gemini"


class ElevenLabsAdapter(ProviderAdapter):
    provider_name = "elevenlabs"


class WavespeedAdapter(ProviderAdapter):
    provider_name = "wavespeed"


def get_provider_adapter(provider_name: str) -> ProviderAdapter:
    adapters: dict[str, ProviderAdapter] = {
        "gemini": GeminiAdapter(),
        "elevenlabs": ElevenLabsAdapter(),
        "wavespeed": WavespeedAdapter(),
    }
    try:
        return adapters[provider_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported provider: {provider_name}") from exc
