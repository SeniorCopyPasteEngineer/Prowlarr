"""Gemini client: video in, LaTeX out.

The video is never downloaded. Gemini ingests YouTube URLs natively, so the
request carries only a ``file_data`` part pointing at the video plus the system
instruction; Google's side does the decoding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

from .config import Settings
from .prompt import (
    CONTINUATION_PROMPT,
    REPAIR_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    USER_PROMPT,
)


class GeminiError(RuntimeError):
    """A request failed in a way the user needs to know about."""


@dataclass
class GenerationResult:
    text: str
    finish_reason: str = ""
    continuations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thought_tokens


def _import_genai():
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise GeminiError(
            "The google-genai SDK is not installed. Run:\n"
            "    pip install -r requirements.txt"
        ) from exc
    return genai, types


_OFFSET_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")


def parse_offset(value: Optional[str]) -> Optional[str]:
    """Normalise a timestamp to the ``<seconds>s`` form the API expects.

    Accepts ``90``, ``90s``, ``1:30`` and ``1:02:03``.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("s"):
        raw = raw[:-1]
    if ":" in raw:
        match = _OFFSET_RE.match(raw)
        if not match:
            raise ValueError(f"Cannot parse timestamp {value!r}.")
        hours, minutes, seconds = match.groups()
        total = int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)
    else:
        try:
            total = float(raw)
        except ValueError:
            raise ValueError(f"Cannot parse timestamp {value!r}.") from None
    if total < 0:
        raise ValueError(f"Timestamp {value!r} is negative.")
    # Integers stay integers so the wire format reads "90s", not "90.0s".
    return f"{int(total)}s" if total == int(total) else f"{total}s"


def _friendly_api_error(exc: Exception) -> GeminiError:
    """Translate SDK exceptions into something actionable."""
    text = str(exc)
    low = text.lower()
    if "api key" in low or "api_key" in low or "unauthenticated" in low or "401" in low:
        hint = (
            "The API key was rejected. Check GEMINI_API_KEY — you can mint one "
            "at https://aistudio.google.com/apikey"
        )
    elif "429" in low or "resource_exhausted" in low or "quota" in low:
        hint = (
            "Rate limited or out of quota. Wait and retry, or use a project "
            "with a higher quota."
        )
    elif "not found" in low and "model" in low:
        hint = (
            "The model id was rejected. Run 'yt2latex models' to list the ids "
            "your key can actually reach, then pass --model."
        )
    elif "youtube" in low or "video" in low or "file_uri" in low:
        hint = (
            "Gemini could not ingest the video. It must be public or unlisted "
            "(not private, not members-only, not age-restricted), and the "
            "free tier caps total YouTube video length per day."
        )
    else:
        hint = "The Gemini request failed."
    return GeminiError(f"{hint}\n\nOriginal error: {text}")


class NotesGenerator:
    """Wraps a Gemini client for the two calls this tool makes."""

    def __init__(self, settings: Settings, verbose: bool = True):
        self.settings = settings
        self.verbose = verbose
        genai, types = _import_genai()
        self._types = types
        if not settings.api_key:
            raise GeminiError(
                "No API key found. Set GEMINI_API_KEY in your environment or "
                "in a .env file (see .env.example), or pass --api-key.\n"
                "Get a key at https://aistudio.google.com/apikey"
            )
        try:
            self._client = genai.Client(
                api_key=settings.api_key,
                http_options=types.HttpOptions(timeout=settings.timeout * 1000),
            )
        except Exception as exc:  # pragma: no cover - constructor rarely fails
            raise _friendly_api_error(exc) from exc

    # ---------------------------------------------------------------- config

    def _thinking_config(self):
        types = self._types
        level = getattr(
            types.ThinkingLevel, self.settings.thinking.upper(), None
        )
        if level is None:  # pragma: no cover - guarded by Settings.validate
            raise GeminiError(f"Unknown thinking level {self.settings.thinking!r}.")
        return types.ThinkingConfig(thinking_level=level)

    def _media_resolution(self):
        if self.settings.media_resolution == "default":
            return None
        return getattr(
            self._types.MediaResolution,
            f"MEDIA_RESOLUTION_{self.settings.media_resolution.upper()}",
            None,
        )

    def _generate_config(self, system_instruction: str):
        types = self._types
        kwargs: dict = {
            "system_instruction": system_instruction,
            "thinking_config": self._thinking_config(),
            "max_output_tokens": self.settings.max_output_tokens,
        }
        if self.settings.temperature is not None:
            kwargs["temperature"] = self.settings.temperature
        media_resolution = self._media_resolution()
        if media_resolution is not None:
            kwargs["media_resolution"] = media_resolution
        return types.GenerateContentConfig(**kwargs)

    def _video_part(self):
        """Build the ``file_data`` part, with optional sampling metadata."""
        types = self._types
        settings = self.settings
        part = types.Part(
            file_data=types.FileData(file_uri=settings.url, mime_type="video/*")
        )
        start = parse_offset(settings.start_offset)
        end = parse_offset(settings.end_offset)
        if start or end or settings.fps:
            part.video_metadata = types.VideoMetadata(
                start_offset=start, end_offset=end, fps=settings.fps
            )
        return part

    # ----------------------------------------------------------- generation

    def _call(self, contents: List[Any], system_instruction: str):
        try:
            return self._client.models.generate_content(
                model=self.settings.model,
                contents=contents,
                config=self._generate_config(system_instruction),
            )
        except GeminiError:
            raise
        except Exception as exc:
            raise _friendly_api_error(exc) from exc

    @staticmethod
    def _response_text(response) -> str:
        """Concatenate answer parts, skipping thought summaries."""
        chunks: List[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "thought", False):
                    continue
                if getattr(part, "text", None):
                    chunks.append(part.text)
        if chunks:
            return "".join(chunks)
        return getattr(response, "text", "") or ""

    @staticmethod
    def _finish_reason(response) -> str:
        for candidate in getattr(response, "candidates", None) or []:
            reason = getattr(candidate, "finish_reason", None)
            if reason is not None:
                return str(getattr(reason, "name", reason))
        return ""

    @staticmethod
    def _accumulate_usage(result: GenerationResult, response) -> None:
        usage = getattr(response, "usage_metadata", None)
        if not usage:
            return
        result.input_tokens += getattr(usage, "prompt_token_count", 0) or 0
        result.output_tokens += getattr(usage, "candidates_token_count", 0) or 0
        result.thought_tokens += getattr(usage, "thoughts_token_count", 0) or 0

    def _guard_finish_reason(self, reason: str, text: str) -> None:
        """Fail loudly on a stop that means the notes are not trustworthy."""
        if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
            raise GeminiError(
                f"Gemini stopped generating: {reason}. The video's content was "
                "blocked by safety filters, so no notes were produced."
            )
        if reason == "RECITATION":
            raise GeminiError(
                "Gemini stopped generating: RECITATION. The response was "
                "suppressed as a verbatim reproduction of copyrighted "
                "material. Try a different video."
            )
        if not text.strip():
            raise GeminiError(
                "Gemini returned an empty response"
                + (f" (finish reason: {reason})." if reason else ".")
            )

    def generate_notes(self) -> GenerationResult:
        """Send the video and return the model's LaTeX, stitching cut-offs."""
        types = self._types
        base = [
            types.Content(
                role="user",
                parts=[self._video_part(), types.Part(text=USER_PROMPT)],
            )
        ]

        response = self._call(base, SYSTEM_INSTRUCTION)
        text = self._response_text(response)
        reason = self._finish_reason(response)
        result = GenerationResult(text=text, finish_reason=reason)
        self._accumulate_usage(result, response)
        self._guard_finish_reason(reason, text)

        # MAX_TOKENS means the document is truncated mid-LaTeX. Ask the model to
        # resume rather than handing the user an uncompilable fragment.
        while (
            reason == "MAX_TOKENS"
            and result.continuations < self.settings.max_continuations
        ):
            result.continuations += 1
            if self.verbose:
                print(
                    f"  output hit the token limit; requesting continuation "
                    f"{result.continuations}/{self.settings.max_continuations}...",
                    flush=True,
                )
            # Rebuild the exchange from the merged text each round so the
            # history stays exactly [video, notes-so-far, "continue"].
            conversation = base + [
                types.Content(role="model", parts=[types.Part(text=text)]),
                types.Content(
                    role="user", parts=[types.Part(text=CONTINUATION_PROMPT)]
                ),
            ]
            response = self._call(conversation, SYSTEM_INSTRUCTION)
            more = self._response_text(response)
            reason = self._finish_reason(response)
            self._accumulate_usage(result, response)
            if not more.strip():
                break
            text = _join_continuation(text, more)

        result.text = text
        result.finish_reason = reason
        return result

    def repair(self, latex: str, errors: str) -> str:
        """Ask the model to fix a document that failed to compile."""
        types = self._types
        message = (
            "The following LaTeX document failed to compile.\n\n"
            "=== COMPILER ERRORS ===\n"
            f"{errors}\n\n"
            "=== LATEX DOCUMENT ===\n"
            f"{latex}"
        )
        contents = [
            types.Content(role="user", parts=[types.Part(text=message)])
        ]
        response = self._call(contents, REPAIR_INSTRUCTION)
        text = self._response_text(response)
        reason = self._finish_reason(response)
        self._guard_finish_reason(reason, text)
        return text

    def list_models(self) -> List[str]:
        try:
            names = []
            for model in self._client.models.list():
                actions = getattr(model, "supported_actions", None) or []
                if actions and "generateContent" not in actions:
                    continue
                name = (getattr(model, "name", "") or "").replace("models/", "")
                if name:
                    names.append(name)
            return sorted(names)
        except Exception as exc:
            raise _friendly_api_error(exc) from exc


def _join_continuation(head: str, tail: str) -> str:
    """Join a resumed response, dropping any overlap the model repeated."""
    tail = tail.lstrip("\n")
    # Strip a code fence the model may have re-opened on resume.
    if tail.startswith("```"):
        tail = tail.split("\n", 1)[1] if "\n" in tail else ""
    # If the model restarted the document, prefer the longer, complete text.
    if "\\documentclass" in tail and "\\documentclass" in head:
        return tail if len(tail) > len(head) else head
    for size in range(min(400, len(head), len(tail)), 20, -1):
        if head.endswith(tail[:size]):
            return head + tail[size:]
    return head + tail
