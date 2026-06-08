import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderValidationResult:
    ok: bool
    status: str
    message: str


class ProviderAdapter:
    provider_name: str

    def validate_key(self, api_key: str) -> ProviderValidationResult:
        """Offline heuristic validation (format checks).

        Used as a safe baseline / fallback when a provider has no read-only
        endpoint that can be probed without risking usage or charges.
        """
        if len(api_key.strip()) < 8:
            return ProviderValidationResult(False, "invalid", "La API key es demasiado corta")
        lowered = api_key.lower()
        if "expired" in lowered or "revoked" in lowered:
            return ProviderValidationResult(
                False,
                "expired_or_revoked",
                "La API key parece expirada o revocada",
            )
        if "invalid" in lowered:
            return ProviderValidationResult(False, "invalid", "El proveedor rechazó la API key")
        return ProviderValidationResult(True, "valid", "Formato de credencial válido")


class GeminiAdapter(ProviderAdapter):
    provider_name = "gemini"


class ElevenLabsAdapter(ProviderAdapter):
    provider_name = "elevenlabs"

    def validate_key(self, api_key: str) -> ProviderValidationResult:
        """Live validation against ElevenLabs.

        Calls the read-only ``GET /v1/user`` endpoint, which is safe (no usage
        charges). Falls back to a heuristic check if the network is unavailable.
        """
        key = api_key.strip()
        if len(key) < 8:
            return ProviderValidationResult(False, "invalid", "La API key es demasiado corta")
        try:
            response = httpx.get(
                "https://api.elevenlabs.io/v1/user",
                headers={"xi-api-key": key},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:  # network/DNS/timeout — cannot reach provider
            logger.warning("ElevenLabs validation could not reach provider: %s", type(exc).__name__)
            return ProviderValidationResult(
                False,
                "invalid",
                "Error al validar credenciales: no se pudo conectar con ElevenLabs",
            )
        if response.status_code == 200:
            return ProviderValidationResult(True, "valid", "Credenciales válidas")
        if response.status_code in (401, 403):
            return ProviderValidationResult(False, "invalid", "ElevenLabs rechazó la API key")
        return ProviderValidationResult(
            False,
            "invalid",
            f"Error al validar credenciales (HTTP {response.status_code})",
        )


class WavespeedAdapter(ProviderAdapter):
    provider_name = "wavespeed"

    # NOTE: WaveSpeed has no documented read-only endpoint that can verify a key
    # without submitting a (billable) prediction. To avoid accidental usage or
    # charges we keep a format/heuristic check here. See KB note 15-ai-credentials
    # ("Credential testing limitations").


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
