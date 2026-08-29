"""Verify the request we build against the real google-genai types.

No network: the client is constructed with a dummy key and only the request
objects are inspected. This is what catches an API-contract drift.
"""

import pytest

pytest.importorskip("google.genai")

from google.genai import types  # noqa: E402

from yt2latex.config import Settings  # noqa: E402
from yt2latex.gemini import (  # noqa: E402
    GeminiError,
    NotesGenerator,
    _friendly_api_error,
    _join_continuation,
    parse_offset,
)
from yt2latex.prompt import SYSTEM_INSTRUCTION  # noqa: E402

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def make_generator(**overrides):
    kwargs = dict(url=URL, api_key="AIza-test-key-not-real")
    kwargs.update(overrides)
    return NotesGenerator(Settings(**kwargs), verbose=False)


def test_requires_an_api_key():
    with pytest.raises(GeminiError, match="No API key"):
        NotesGenerator(Settings(url=URL, api_key=None), verbose=False)


def test_thinking_level_high_is_the_default():
    config = make_generator()._generate_config(SYSTEM_INSTRUCTION)
    assert config.thinking_config.thinking_level == types.ThinkingLevel.HIGH
    # Gemini 3 rejects thinking_level and thinking_budget together.
    assert config.thinking_config.thinking_budget is None


@pytest.mark.parametrize(
    "level,expected",
    [
        ("high", types.ThinkingLevel.HIGH),
        ("medium", types.ThinkingLevel.MEDIUM),
        ("low", types.ThinkingLevel.LOW),
        ("minimal", types.ThinkingLevel.MINIMAL),
    ],
)
def test_thinking_levels_map_to_sdk_enum(level, expected):
    config = make_generator(thinking=level)._generate_config(SYSTEM_INSTRUCTION)
    assert config.thinking_config.thinking_level == expected


def test_system_instruction_is_sent_verbatim():
    config = make_generator()._generate_config(SYSTEM_INSTRUCTION)
    assert config.system_instruction == SYSTEM_INSTRUCTION
    assert "ABSOLUTE SOURCE ISOLATION" in config.system_instruction
    assert config.max_output_tokens == 64000


def test_temperature_omitted_unless_set():
    assert make_generator()._generate_config(SYSTEM_INSTRUCTION).temperature is None
    assert make_generator(temperature=0.2)._generate_config(
        SYSTEM_INSTRUCTION
    ).temperature == pytest.approx(0.2)


def test_video_part_carries_the_youtube_url():
    part = make_generator()._video_part()
    assert part.file_data.file_uri == URL
    assert part.video_metadata is None


def test_video_metadata_only_when_sampling_is_requested():
    part = make_generator(start_offset="1:30", end_offset="2:00", fps=0.5)._video_part()
    assert part.video_metadata.start_offset == "90s"
    assert part.video_metadata.end_offset == "120s"
    assert part.video_metadata.fps == 0.5


def test_media_resolution_maps_to_sdk_enum():
    assert make_generator()._media_resolution() is None
    assert (
        make_generator(media_resolution="low")._media_resolution()
        == types.MediaResolution.MEDIA_RESOLUTION_LOW
    )


def test_response_text_skips_thought_parts():
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    parts=[
                        types.Part(text="internal reasoning", thought=True),
                        types.Part(text="\\documentclass{article}"),
                    ]
                )
            )
        ]
    )
    assert NotesGenerator._response_text(response) == "\\documentclass{article}"


def test_finish_reason_is_read_from_the_candidate():
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.MAX_TOKENS)]
    )
    assert NotesGenerator._finish_reason(response) == "MAX_TOKENS"


@pytest.mark.parametrize("reason", ["SAFETY", "PROHIBITED_CONTENT", "RECITATION"])
def test_blocked_finish_reasons_raise(reason):
    with pytest.raises(GeminiError):
        make_generator()._guard_finish_reason(reason, "some text")


def test_empty_response_raises():
    with pytest.raises(GeminiError, match="empty response"):
        make_generator()._guard_finish_reason("STOP", "   ")


@pytest.mark.parametrize(
    "message,expected",
    [
        ("401 UNAUTHENTICATED: bad api key", "aistudio.google.com/apikey"),
        ("429 RESOURCE_EXHAUSTED quota", "Rate limited"),
        ("404 model not found", "yt2latex models"),
        ("failed to fetch youtube video", "public or unlisted"),
    ],
)
def test_api_errors_get_actionable_hints(message, expected):
    assert expected in str(_friendly_api_error(RuntimeError(message)))


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), ("", None), ("90", "90s"), ("90s", "90s"),
     ("1:30", "90s"), ("1:02:03", "3723s"), ("12.5", "12.5s")],
)
def test_parse_offset(value, expected):
    assert parse_offset(value) == expected


@pytest.mark.parametrize("bad", ["abc", "1:2:3:4", "-5"])
def test_parse_offset_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_offset(bad)


def test_join_continuation_removes_repeated_overlap():
    head = "\\section{A}\nsome text that the model will repeat verbatim here"
    tail = "some text that the model will repeat verbatim here\nmore content"
    assert _join_continuation(head, tail) == head + "\nmore content"


def test_join_continuation_handles_no_overlap():
    assert _join_continuation("abc", "def") == "abcdef"


def test_join_continuation_drops_reopened_fence():
    assert _join_continuation("abc", "```latex\ndef") == "abcdef"


def test_join_continuation_prefers_complete_restart():
    head = "\\documentclass{a}\n\\begin{document}\ncut off"
    tail = "\\documentclass{a}\n\\begin{document}\nfull text\n\\end{document}"
    assert _join_continuation(head, tail) == tail
