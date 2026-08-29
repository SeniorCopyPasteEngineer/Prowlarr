"""Runtime configuration for a single yt2latex run."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Released GA on 2026-08-13; 1M-token context, tunable thinking levels.
DEFAULT_MODEL = "gemini-3.7-flash"
DEFAULT_THINKING = "high"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")

# Gemini 3.7 Flash caps output at 64k tokens. A dense set of LaTeX notes for a
# long lecture can genuinely reach that, so we ask for the ceiling and stitch
# continuations when the model still runs out of room.
DEFAULT_MAX_OUTPUT_TOKENS = 64000

API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")


class ConfigError(RuntimeError):
    """Raised for an unusable configuration (missing key, bad value)."""


def load_dotenv(path: Path) -> None:
    """Load ``KEY=value`` pairs from *path* without overriding real env vars.

    Deliberately tiny: it keeps python-dotenv out of the dependency list for
    what amounts to a handful of lines.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def find_api_key() -> Optional[str]:
    for var in API_KEY_VARS:
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    return None


@dataclass
class Settings:
    """Everything one run needs, already validated."""

    url: str
    out_dir: Path = Path("out")
    basename: str = "notes"
    model: str = DEFAULT_MODEL
    thinking: str = DEFAULT_THINKING
    temperature: Optional[float] = None
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    media_resolution: str = "default"
    fps: Optional[float] = None
    start_offset: Optional[str] = None
    end_offset: Optional[str] = None
    engine: str = "auto"
    repair_attempts: int = 2
    max_continuations: int = 3
    keep_tex: bool = True
    keep_aux: bool = False
    shell_escape: bool = False
    tex_only: bool = False
    timeout: int = 1800
    compile_timeout: int = 300
    api_key: Optional[str] = field(default=None, repr=False)

    def validate(self) -> None:
        if self.thinking not in THINKING_LEVELS:
            raise ConfigError(
                f"Unknown thinking level {self.thinking!r}. "
                f"Choose one of: {', '.join(THINKING_LEVELS)}."
            )
        if self.media_resolution not in MEDIA_RESOLUTIONS:
            raise ConfigError(
                f"Unknown media resolution {self.media_resolution!r}. "
                f"Choose one of: {', '.join(MEDIA_RESOLUTIONS)}."
            )
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ConfigError("--temperature must be between 0.0 and 2.0.")
        if self.fps is not None and self.fps <= 0:
            raise ConfigError("--fps must be greater than 0.")
        if self.repair_attempts < 0:
            raise ConfigError("--repair must be 0 or greater.")
        if self.max_continuations < 0:
            raise ConfigError("--max-continuations must be 0 or greater.")
        if self.max_output_tokens <= 0:
            raise ConfigError("--max-output-tokens must be greater than 0.")
