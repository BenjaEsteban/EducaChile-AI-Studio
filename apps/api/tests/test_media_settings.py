from app.modules.generation.media_settings import (
    DEFAULT_MEDIA_SETTINGS,
    SUBTITLE_MAX_FONT_SIZE,
    SUBTITLE_MIN_FONT_SIZE,
    build_subtitle_force_style,
    normalize_media_settings,
)


def test_normalize_none_returns_defaults():
    norm = normalize_media_settings(None)
    assert norm["background_music"]["enabled"] is False
    assert norm["background_music"]["asset_id"] is None
    assert norm["subtitles"]["enabled"] is True  # preserves prior behavior
    assert norm["subtitles"]["font_size"] == DEFAULT_MEDIA_SETTINGS["subtitles"]["font_size"]


def test_normalize_clamps_and_validates():
    norm = normalize_media_settings(
        {
            "background_music": {
                "enabled": True,
                "volume": 5.0,            # clamp to 1.0
                "fade_out_seconds": -2,   # clamp to 0
                "loop": "yes",
            },
            "subtitles": {
                "font_size": 999,         # clamp to max
                "background_opacity": 2,  # clamp to 1
                "position": "weird",      # fallback to bottom
                "text_color": "not-a-color",
                "background_color": "00ff00",
            },
        }
    )
    assert norm["background_music"]["volume"] == 1.0
    assert norm["background_music"]["fade_out_seconds"] == 0.0
    assert norm["background_music"]["loop"] is True
    assert norm["subtitles"]["font_size"] == SUBTITLE_MAX_FONT_SIZE
    assert norm["subtitles"]["background_opacity"] == 1.0
    assert norm["subtitles"]["position"] == "bottom"
    assert norm["subtitles"]["text_color"] == "#FFFFFF"  # invalid -> default
    assert norm["subtitles"]["background_color"] == "#00FF00"  # normalized + uppercased


def test_font_size_min_clamp():
    norm = normalize_media_settings({"subtitles": {"font_size": 1}})
    assert norm["subtitles"]["font_size"] == SUBTITLE_MIN_FONT_SIZE


def test_build_subtitle_force_style_reflects_settings():
    style = build_subtitle_force_style(
        {
            "font_family": "Verdana",
            "font_size": 30,
            "text_color": "#FF0000",
            "background_color": "#000000",
            "background_opacity": 1.0,
            "position": "top",
        }
    )
    assert "FontName=Verdana" in style
    assert "FontSize=30" in style
    # red text → ASS &H00 0000FF (BBGGRR)
    assert "PrimaryColour=&H000000FF" in style
    # opaque background → alpha 00
    assert "BackColour=&H00000000" in style
    # top position → alignment 8
    assert "Alignment=8" in style


def test_build_subtitle_force_style_opacity_alpha():
    style = build_subtitle_force_style(
        {"background_color": "#000000", "background_opacity": 0.0}
    )
    # fully transparent background → alpha FF
    assert "BackColour=&HFF000000" in style


def test_build_subtitle_force_style_without_box_is_unchanged():
    # Backward-compat guard: no box → identical to legacy single-margin output.
    style = build_subtitle_force_style({"position": "bottom"})
    assert "MarginV=40" in style
    assert "MarginL" not in style
    assert "MarginR" not in style


def test_build_subtitle_force_style_with_box_bottom():
    style = build_subtitle_force_style(
        {"position": "bottom"},
        box={"x": 40, "y": 400, "width": 880, "height": 100},
        canvas_width=960,
        canvas_height=540,
        output_width=1920,
        output_height=1080,
    )
    # scale_x = 1920/960 = 2, scale_y = 1080/540 = 2
    # MarginL = 40*2 = 80; MarginR = (960-40-880)*2 = 80
    assert "MarginL=80" in style
    assert "MarginR=80" in style
    # bottom edge of box = 400+100=500; distance to canvas bottom (540)=40 -> *2 = 80
    assert "MarginV=80" in style


def test_build_subtitle_force_style_with_box_top():
    style = build_subtitle_force_style(
        {"position": "top"},
        box={"x": 0, "y": 20, "width": 960, "height": 80},
        canvas_width=960,
        canvas_height=540,
    )
    # MarginV = box_y * scale_y = 20*2 = 40
    assert "MarginV=40" in style
    assert "MarginL=0" in style
    assert "MarginR=0" in style


def test_build_subtitle_force_style_with_box_center_ignores_margin_v():
    style = build_subtitle_force_style(
        {"position": "center"},
        box={"x": 100, "y": 200, "width": 760, "height": 100},
        canvas_width=960,
        canvas_height=540,
    )
    assert "MarginV=0" in style
    assert "MarginL" in style  # horizontal box still applied


def test_build_subtitle_force_style_invalid_box_falls_back():
    style = build_subtitle_force_style(
        {"position": "bottom"},
        box={"x": "bad", "width": 0},
    )
    assert "MarginV=40" in style
    assert "MarginL" not in style
