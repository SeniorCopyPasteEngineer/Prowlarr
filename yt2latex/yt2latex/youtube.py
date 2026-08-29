"""YouTube URL parsing and normalization.

The Gemini API ingests a YouTube video by URL, so the only job here is to
recognise the accepted URL shapes, pull out the 11-character video id, and hand
back a canonical ``watch?v=`` URL. Anything we cannot positively identify as a
YouTube video is rejected early, before we spend a request on it.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

# /shorts/<id>, /live/<id>, /embed/<id>, /v/<id>
_PATH_PREFIXES = ("shorts", "live", "embed", "v")


class InvalidYouTubeURL(ValueError):
    """Raised when the input is not a recognisable YouTube video URL."""


def extract_video_id(url: str) -> str:
    """Return the 11-character video id for *url*.

    Raises:
        InvalidYouTubeURL: if *url* is not a recognisable YouTube video URL.
    """
    raw = (url or "").strip()
    if not raw:
        raise InvalidYouTubeURL("No URL provided.")

    # A bare video id is a convenient shorthand and is unambiguous.
    if VIDEO_ID_RE.match(raw):
        return raw

    if "://" not in raw:
        raw = "https://" + raw

    parsed = urlparse(raw)
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if host not in _HOSTS:
        raise InvalidYouTubeURL(
            f"{url!r} is not a YouTube URL (host {host or '?'!r}). "
            "Expected a youtube.com or youtu.be link."
        )

    segments = [s for s in parsed.path.split("/") if s]

    candidate = None
    if host in ("youtu.be", "www.youtu.be"):
        candidate = segments[0] if segments else None
    elif segments and segments[0] == "watch":
        candidate = parse_qs(parsed.query).get("v", [None])[0]
    elif len(segments) >= 2 and segments[0] in _PATH_PREFIXES:
        candidate = segments[1]
    elif not segments:
        candidate = parse_qs(parsed.query).get("v", [None])[0]

    if not candidate:
        raise InvalidYouTubeURL(
            f"Could not find a video id in {url!r}. Supported forms: "
            "watch?v=ID, youtu.be/ID, /shorts/ID, /live/ID, /embed/ID."
        )
    if not VIDEO_ID_RE.match(candidate):
        raise InvalidYouTubeURL(
            f"{candidate!r} is not a valid YouTube video id "
            "(expected 11 characters of A-Z, a-z, 0-9, '_' or '-')."
        )
    return candidate


def normalize_url(url: str) -> str:
    """Return the canonical ``https://www.youtube.com/watch?v=<id>`` form."""
    return f"https://www.youtube.com/watch?v={extract_video_id(url)}"
