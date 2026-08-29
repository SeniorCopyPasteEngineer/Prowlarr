import pytest

from yt2latex.youtube import InvalidYouTubeURL, extract_video_id, normalize_url

VALID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VALID}",
        f"http://youtube.com/watch?v={VALID}",
        f"https://m.youtube.com/watch?v={VALID}&list=PLabc",
        f"https://music.youtube.com/watch?v={VALID}",
        f"https://youtu.be/{VALID}",
        f"https://youtu.be/{VALID}?t=42",
        f"https://www.youtube.com/shorts/{VALID}",
        f"https://www.youtube.com/live/{VALID}",
        f"https://www.youtube.com/embed/{VALID}",
        f"https://www.youtube.com/v/{VALID}",
        f"youtube.com/watch?v={VALID}",
        f"  https://www.youtube.com/watch?v={VALID}  ",
        VALID,
    ],
)
def test_accepts_known_url_shapes(url):
    assert extract_video_id(url) == VALID


def test_normalize_is_canonical():
    assert normalize_url(f"https://youtu.be/{VALID}?t=9") == (
        f"https://www.youtube.com/watch?v={VALID}"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "https://vimeo.com/12345678",
        "https://example.com/watch?v=" + VALID,
        "https://www.youtube.com/",
        "https://www.youtube.com/feed/subscriptions",
        "https://youtu.be/tooshort",
        "https://www.youtube.com/watch?v=has spaces!",
    ],
)
def test_rejects_non_video_urls(url):
    with pytest.raises(InvalidYouTubeURL):
        extract_video_id(url)


def test_hostname_with_port_and_userinfo():
    assert extract_video_id(f"https://user@www.youtube.com:443/watch?v={VALID}") == VALID
