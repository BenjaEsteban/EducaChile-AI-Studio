from app.modules.composer.subtitles import (
    build_srt_content,
    build_subtitle_cues,
    format_srt_timestamp,
    split_dialogue_for_subtitles,
)


def test_format_srt_timestamp():
    assert format_srt_timestamp(3661.345) == "01:01:01,345"
    assert format_srt_timestamp(-2.0) == "00:00:00,000"


def test_split_dialogue_for_subtitles_uses_readable_phrases():
    dialogue = (
        "Hola estudiantes. Hoy revisaremos fracciones equivalentes, con ejemplos sencillos "
        "y una práctica breve para el cierre."
    )
    phrases = split_dialogue_for_subtitles(dialogue, max_words_per_phrase=6, max_chars_per_phrase=44)
    assert phrases
    assert all(len(phrase.split()) <= 6 for phrase in phrases)


def test_build_subtitle_cues_uses_proportional_word_timing():
    dialogue = "Uno dos tres. Cuatro cinco seis siete ocho nueve."
    cues = build_subtitle_cues(dialogue, 12.0)
    assert len(cues) == 2
    first_duration = cues[0].end_seconds - cues[0].start_seconds
    second_duration = cues[1].end_seconds - cues[1].start_seconds
    assert second_duration > first_duration
    assert cues[-1].end_seconds == 12.0


def test_build_srt_content_handles_empty_dialogue():
    assert build_srt_content("", 15.0) == ""
    assert build_srt_content("   ", 15.0) == ""
