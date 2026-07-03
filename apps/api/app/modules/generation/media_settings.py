"""Project-level media settings: background music + subtitle styling.

Stored as JSON on ``VideoGenerationSettings.media_settings`` (additive column).
This module centralizes defaults, validation/clamping, and the FFmpeg subtitle
``force_style`` builder so the API, the worker pipeline, and tests share one
source of truth. All values degrade safely to defaults, so an empty/missing
settings blob reproduces the previous generation behavior.
"""

from __future__ import annotations

import re

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

# Subtitle font-size guard rails (ASS FontSize units).
SUBTITLE_MIN_FONT_SIZE = 10
SUBTITLE_MAX_FONT_SIZE = 72
SUBTITLE_POSITIONS = ("bottom", "center", "top")

DEFAULT_MEDIA_SETTINGS: dict = {
    "background_music": {
        "enabled": False,
        "asset_id": None,
        "loop": True,
        "volume": 0.35,            # 0.0 – 1.0
        "fade_out_enabled": True,
        "fade_out_seconds": 3.0,
    },
    "subtitles": {
        # Subtitles were always burned in before this feature; default enabled
        # so existing generation behavior is preserved when no setting is saved.
        "enabled": True,
        "font_family": "Arial",
        "font_size": 24,
        "text_color": "#FFFFFF",
        "background_color": "#000000",
        "background_opacity": 0.5,  # 0.0 – 1.0
        "position": "bottom",
    },
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _normalize_hex(value, default: str) -> str:
    if isinstance(value, str):
        match = _HEX_RE.match(value.strip())
        if match:
            return f"#{match.group(1).upper()}"
    return default


def normalize_media_settings(raw: dict | None) -> dict:
    """Merge a raw (possibly partial/untrusted) blob with defaults + clamps."""
    raw = raw if isinstance(raw, dict) else {}
    music_raw = raw.get("background_music") if isinstance(raw.get("background_music"), dict) else {}
    subs_raw = raw.get("subtitles") if isinstance(raw.get("subtitles"), dict) else {}
    dm = DEFAULT_MEDIA_SETTINGS["background_music"]
    ds = DEFAULT_MEDIA_SETTINGS["subtitles"]

    asset_id = music_raw.get("asset_id")
    asset_id = str(asset_id) if asset_id else None

    font_size = int(_as_float(subs_raw.get("font_size"), ds["font_size"]))
    font_size = int(_clamp(font_size, SUBTITLE_MIN_FONT_SIZE, SUBTITLE_MAX_FONT_SIZE))

    position = subs_raw.get("position")
    position = position if position in SUBTITLE_POSITIONS else ds["position"]

    font_family = subs_raw.get("font_family")
    font_family = font_family.strip() if isinstance(font_family, str) and font_family.strip() else ds["font_family"]

    return {
        "background_music": {
            "enabled": _as_bool(music_raw.get("enabled"), dm["enabled"]),
            "asset_id": asset_id,
            "loop": _as_bool(music_raw.get("loop"), dm["loop"]),
            "volume": round(_clamp(_as_float(music_raw.get("volume"), dm["volume"]), 0.0, 1.0), 3),
            "fade_out_enabled": _as_bool(music_raw.get("fade_out_enabled"), dm["fade_out_enabled"]),
            "fade_out_seconds": round(
                _clamp(_as_float(music_raw.get("fade_out_seconds"), dm["fade_out_seconds"]), 0.0, 30.0),
                2,
            ),
        },
        "subtitles": {
            "enabled": _as_bool(subs_raw.get("enabled"), ds["enabled"]),
            "font_family": font_family,
            "font_size": font_size,
            "text_color": _normalize_hex(subs_raw.get("text_color"), ds["text_color"]),
            "background_color": _normalize_hex(subs_raw.get("background_color"), ds["background_color"]),
            "background_opacity": round(
                _clamp(_as_float(subs_raw.get("background_opacity"), ds["background_opacity"]), 0.0, 1.0),
                2,
            ),
            "position": position,
        },
    }


def _hex_to_ass(color: str, alpha: int = 0) -> str:
    """Convert ``#RRGGBB`` to an ASS ``&HAABBGGRR`` colour (alpha 0 = opaque)."""
    match = _HEX_RE.match(color or "")
    rr, gg, bb = ("FF", "FF", "FF")
    if match:
        hexv = match.group(1).upper()
        rr, gg, bb = hexv[0:2], hexv[2:4], hexv[4:6]
    aa = f"{max(0, min(alpha, 255)):02X}"
    return f"&H{aa}{bb}{gg}{rr}"


def _normalize_subtitle_box(box: dict | None) -> tuple[float, float, float, float] | None:
    """Validate a per-slide subtitle safe-area box (canvas units)."""
    if not isinstance(box, dict):
        return None
    try:
        x = float(box.get("x"))
        y = float(box.get("y"))
        width = float(box.get("width"))
        height = float(box.get("height"))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def build_subtitle_force_style(
    subtitles: dict,
    *,
    box: dict | None = None,
    canvas_width: float = 960.0,
    canvas_height: float = 540.0,
    output_width: int = 1920,
    output_height: int = 1080,
) -> str:
    """Build an FFmpeg ``subtitles=...:force_style='...'`` style string.

    ``box`` is an optional per-slide safe-area rectangle (``{x, y, width,
    height}`` in canvas units, same coordinate space as the avatar overlay).
    When provided, it is translated into libass ``MarginL``/``MarginR`` (so
    subtitle text wraps within that horizontal region) and ``MarginV`` (derived
    from whichever edge matches the configured vertical ``position``) — this
    reuses the exact same ``subtitles`` FFmpeg filter already in place, adding
    only extra numeric style parameters (no new filter, no extra FFmpeg pass).
    Without a box, output is byte-identical to the pre-existing behavior.
    """
    subs = normalize_media_settings({"subtitles": subtitles})["subtitles"]
    alignment = {"bottom": 2, "center": 5, "top": 8}[subs["position"]]
    # ASS alpha: 00 = opaque, FF = transparent.
    bg_alpha = int(round((1.0 - subs["background_opacity"]) * 255))
    primary = _hex_to_ass(subs["text_color"], alpha=0)
    back = _hex_to_ass(subs["background_color"], alpha=bg_alpha)

    normalized_box = _normalize_subtitle_box(box)
    margin_lr = ""
    if normalized_box is not None:
        box_x, box_y, box_width, box_height = normalized_box
        scale_x = output_width / max(float(canvas_width), 1.0)
        scale_y = output_height / max(float(canvas_height), 1.0)
        margin_l = max(0, round(box_x * scale_x))
        margin_r = max(0, round((canvas_width - box_x - box_width) * scale_x))
        margin_lr = f",MarginL={margin_l},MarginR={margin_r}"
        if subs["position"] == "top":
            margin_v = max(0, round(box_y * scale_y))
        elif subs["position"] == "center":
            margin_v = 0
        else:  # bottom
            margin_v = max(0, round((canvas_height - box_y - box_height) * scale_y))
    else:
        margin_v = 40 if subs["position"] != "center" else 0

    # BorderStyle=3 → opaque box behind text using BackColour.
    return (
        f"FontName={subs['font_family']},FontSize={subs['font_size']},"
        f"PrimaryColour={primary},BorderStyle=3,Outline=1,Shadow=0,"
        f"BackColour={back}{margin_lr},Alignment={alignment},MarginV={margin_v}"
    )
