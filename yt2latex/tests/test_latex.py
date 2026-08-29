import pytest

from yt2latex.latex import (
    NoLatexFound,
    check_document,
    close_truncated_document,
    extract_latex,
)

DOC = "\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}"


def test_extracts_bare_document():
    assert extract_latex(DOC).source.strip() == DOC
    assert extract_latex(DOC).truncated is False


def test_extracts_from_latex_fence():
    response = f"Here are the notes.\n\n```latex\n{DOC}\n```\n\nHope that helps!"
    assert extract_latex(response).source.strip() == DOC


def test_extracts_from_untagged_fence():
    response = f"```\n{DOC}\n```"
    assert extract_latex(response).source.strip() == DOC


def test_strips_leading_commentary_without_fence():
    response = f"Reasoning about the video...\n{DOC}\nThat is all."
    assert extract_latex(response).source.strip() == DOC


def test_prefers_longest_fenced_block():
    small = "```latex\n\\documentclass{a}\\begin{document}x\\end{document}\n```"
    response = f"{small}\n\n```latex\n{DOC}\n```"
    assert "Hello" in extract_latex(response).source


def test_unterminated_fence_still_yields_document():
    assert "Hello" in extract_latex(f"```latex\n{DOC}").source


def test_flags_truncated_document():
    doc = extract_latex("\\documentclass{article}\n\\begin{document}\n\\begin{tcolorbox}Hi")
    assert doc.truncated is True


@pytest.mark.parametrize("response", ["", "   ", "I could not watch that video."])
def test_raises_without_documentclass(response):
    with pytest.raises(NoLatexFound):
        extract_latex(response)


def test_close_truncated_closes_open_environments_in_order():
    source = (
        "\\documentclass{article}\n\\begin{document}\n"
        "\\begin{itemize}\n\\item a\n\\begin{tcolorbox}\ntext"
    )
    closed = close_truncated_document(source)
    assert closed.index("\\end{tcolorbox}") < closed.index("\\end{itemize}")
    assert closed.rstrip().endswith("\\end{document}")
    assert not check_document(closed)


def test_close_truncated_drops_partial_trailing_command():
    closed = close_truncated_document("\\documentclass{a}\n\\begin{document}\nx \\subsec")
    assert "\\subsec\n" not in closed
    assert closed.rstrip().endswith("\\end{document}")


def test_close_is_noop_when_already_complete():
    assert close_truncated_document(DOC) == DOC


def test_check_document_reports_problems():
    warnings = check_document("\\documentclass{a}\\begin{document}\\begin{itemize}")
    assert any("end{document}" in w for w in warnings)
    assert any("unclosed" in w for w in warnings)


def test_check_document_clean_for_valid_doc():
    assert check_document(DOC) == []
