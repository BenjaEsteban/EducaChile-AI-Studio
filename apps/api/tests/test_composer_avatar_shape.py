from app.modules.composer.service import build_avatar_shape_filters
from app.modules.generation.pipeline import (
    _avatar_overlay_from_metadata,
    _slide_avatar_border_color,
    _slide_avatar_border_radius,
)


def test_no_shape_filters_for_square_avatar_without_border():
    shape, border_chain, margin = build_avatar_shape_filters(400, 400, 0.0, None, 5.0)
    assert shape == ""
    assert border_chain == ""
    assert margin == 0


def test_circular_avatar_produces_alpha_mask():
    shape, border_chain, margin = build_avatar_shape_filters(400, 400, 50.0, None, 5.0)
    assert "geq" in shape
    assert "format=rgba" in shape
    # 50% of min(400,400) = full circle radius 200
    assert "200" in shape
    assert border_chain == ""
    assert margin == 0


def test_border_color_produces_colored_plate_chain():
    shape, border_chain, margin = build_avatar_shape_filters(400, 300, 50.0, "#ff0000", 5.0)
    assert margin > 0
    assert f"color=c=#ff0000:s={400 + 2 * margin}x{300 + 2 * margin}" in border_chain
    assert "[avatarframed]" in border_chain
    # Plate is rounded too when the avatar is rounded
    assert "geq" in border_chain


def test_slide_avatar_border_metadata_helpers():
    assert _slide_avatar_border_radius({"avatar_border_radius": 50}) == 50.0
    assert _slide_avatar_border_radius({"avatar_border_radius": 120}) == 50.0
    assert _slide_avatar_border_radius({}) == 0.0
    assert _slide_avatar_border_radius({"avatar_border_radius": "bad"}) == 0.0

    assert _slide_avatar_border_color({"avatar_border_color": "#A1B2C3"}) == "#A1B2C3"
    assert _slide_avatar_border_color({"avatar_border_color": "a1b2c3"}) == "#a1b2c3"
    assert _slide_avatar_border_color({"avatar_border_color": "red"}) is None
    assert _slide_avatar_border_color({"avatar_border_color": "#fff"}) is None
    assert _slide_avatar_border_color({}) is None


def test_avatar_offset_y_shifts_overlay_without_breaking_clamp():
    metadata = {
        "canvas": {
            "width": 960,
            "height": 540,
            "avatar": {"x": 0, "y": 0, "width": 960, "height": 540},
        },
        "avatar_offset_y": -100,
    }
    overlay = _avatar_overlay_from_metadata(metadata, "1080p")
    # Full slide scaled to 1920x1080; offset -100 canvas units → -200 output px
    assert overlay["width"] == 1920
    assert overlay["height"] == 1080
    assert overlay["x"] == 0
    assert overlay["y"] == -200

    # Without offset the previous clamped behavior is preserved
    metadata.pop("avatar_offset_y")
    overlay = _avatar_overlay_from_metadata(metadata, "1080p")
    assert overlay["y"] == 0


def test_border_width_px_controls_plate_margin():
    _, chain_thin, margin_thin = build_avatar_shape_filters(400, 400, 0.0, "#000000", 5.0, border_width_px=4)
    _, chain_thick, margin_thick = build_avatar_shape_filters(400, 400, 0.0, "#000000", 5.0, border_width_px=16)
    assert margin_thin == 4
    assert margin_thick == 16
    assert "s=408x408" in chain_thin
    assert "s=432x432" in chain_thick


def test_slide_avatar_border_width_px_scales_to_output():
    from app.modules.generation.pipeline import _slide_avatar_border_width_px

    # default 6 canvas units, canvas 540 → 1080p scale x2 → 12 px
    assert _slide_avatar_border_width_px({}, "1080p") == 12
    assert _slide_avatar_border_width_px({"avatar_border_width": 8}, "1080p") == 16
    # 720p scale = 720/540 ≈ 1.333 → 6*1.333 ≈ 8
    assert _slide_avatar_border_width_px({"avatar_border_width": 6}, "720p") == 8
    # invalid falls back to default 6 → 12 px at 1080p
    assert _slide_avatar_border_width_px({"avatar_border_width": "x"}, "1080p") == 12


def test_slide_subtitle_box_reads_canvas_metadata():
    from app.modules.generation.pipeline import _slide_canvas_size, _slide_subtitle_box

    metadata = {
        "canvas": {
            "width": 960,
            "height": 540,
            "subtitleBox": {"x": 20, "y": 400, "width": 900, "height": 90},
        }
    }
    assert _slide_subtitle_box(metadata) == {"x": 20, "y": 400, "width": 900, "height": 90}
    assert _slide_canvas_size(metadata) == (960.0, 540.0)

    # Missing/invalid box or canvas -> None / defaults
    assert _slide_subtitle_box({}) is None
    assert _slide_subtitle_box({"canvas": {}}) is None
    assert _slide_canvas_size({}) == (960.0, 540.0)
