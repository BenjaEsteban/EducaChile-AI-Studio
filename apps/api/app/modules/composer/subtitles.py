import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_seconds: float
    end_seconds: float
    text: str


def format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3_600_000
    remainder = total_ms % 3_600_000
    minutes = remainder // 60_000
    remainder %= 60_000
    secs = remainder // 1000
    millis = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def split_dialogue_for_subtitles(
    dialogue: str,
    *,
    max_words_per_phrase: int = 14,
    max_chars_per_phrase: int = 80,
) -> list[str]:
    normalized = " ".join((dialogue or "").split())
    if not normalized:
        return []

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]
    phrases: list[str] = []
    for sentence in sentences:
        phrases.extend(
            _split_sentence(
                sentence,
                max_words_per_phrase=max_words_per_phrase,
                max_chars_per_phrase=max_chars_per_phrase,
            )
        )
    return phrases


def build_subtitle_cues(dialogue: str, duration_seconds: float) -> list[SubtitleCue]:
    if duration_seconds <= 0:
        return []
    phrases = split_dialogue_for_subtitles(dialogue)
    if not phrases:
        return []

    weights = [max(_word_count(text), 1) for text in phrases]
    total_weight = sum(weights)
    if total_weight <= 0:
        return []

    cues: list[SubtitleCue] = []
    current_start = 0.0
    for index, (text, weight) in enumerate(zip(phrases, weights, strict=True), 1):
        if index == len(phrases):
            end_time = duration_seconds
        else:
            portion = duration_seconds * (weight / total_weight)
            end_time = min(duration_seconds, current_start + portion)
        if end_time <= current_start:
            end_time = min(duration_seconds, current_start + 0.2)
        cues.append(
            SubtitleCue(
                index=index,
                start_seconds=current_start,
                end_seconds=end_time,
                text=text,
            )
        )
        current_start = end_time
    return cues


def build_srt_content(dialogue: str, duration_seconds: float) -> str:
    cues = build_subtitle_cues(dialogue, duration_seconds)
    if not cues:
        return ""
    blocks: list[str] = []
    for cue in cues:
        blocks.append(
            "\n".join(
                [
                    str(cue.index),
                    f"{format_srt_timestamp(cue.start_seconds)} --> {format_srt_timestamp(cue.end_seconds)}",
                    cue.text,
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def _split_sentence(
    sentence: str,
    *,
    max_words_per_phrase: int,
    max_chars_per_phrase: int,
) -> list[str]:
    sentence = sentence.strip()
    if not sentence:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    words = sentence.split()
    for word in words:
        pending = len(current) + 1
        next_len = current_len + (1 if current else 0) + len(word)
        if current and (pending > max_words_per_phrase or next_len > max_chars_per_phrase):
            chunks.append(" ".join(current).strip())
            current = [word]
            current_len = len(word)
            continue
        current.append(word)
        current_len = next_len
    if current:
        chunks.append(" ".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _word_count(text: str) -> int:
    return len([part for part in text.split() if part])
