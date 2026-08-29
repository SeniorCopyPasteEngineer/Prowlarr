"""End-to-end pipeline tests with a stand-in for the model.

These exercise the real LaTeX toolchain: a fake generator supplies the
"model" output, and the pipeline genuinely compiles it to a PDF.
"""

import pytest

from yt2latex.compiler import available_engines
from yt2latex.config import Settings
from yt2latex.gemini import GenerationResult
from yt2latex.pipeline import run

needs_latex = pytest.mark.skipif(
    not available_engines(), reason="no LaTeX engine installed"
)

VALID_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

GOOD_NOTES = r"""```latex
\documentclass[11pt]{article}
\usepackage{amsmath,amssymb}
\usepackage{tcolorbox}
\tcbuselibrary{most}
\usepackage{tikz}
\usetikzlibrary{arrows.meta,positioning}
\begin{document}
\section{Stability of the Explicit Euler Scheme}
\begin{tcolorbox}[colback=blue!5,title=Update]
$y_{n+1} = y_n + h\lambda y_n$
\end{tcolorbox}
\begin{tikzpicture}[>=Stealth]
\node (a) {$y_n$}; \node (b) [right=2cm of a] {$y_{n+1}$};
\draw[->] (a) -- (b) node[midway,above] {$1+h\lambda$};
\end{tikzpicture}
\end{document}
```"""

BROKEN_NOTES = r"""\documentclass{article}
\begin{document}
\section{Broken}
\undefinedMacroFromTheModel{x}
\end{document}
"""

FIXED_NOTES = r"""\documentclass{article}
\begin{document}
\section{Repaired}
x
\end{document}
"""

TRUNCATED_NOTES = r"""\documentclass{article}
\usepackage{tcolorbox}
\begin{document}
\section{Cut Off}
\begin{tcolorbox}
The lecturer defines $\varepsilon$ as"""

MISSING_PKG_NOTES = r"""\documentclass{article}
\usepackage{definitelynotarealpackagename}
\begin{document}
Hi
\end{document}
"""


class FakeGenerator:
    """Stands in for NotesGenerator; records how it was used."""

    def __init__(self, responses, repairs=()):
        self._responses = list(responses)
        self._repairs = list(repairs)
        self.repair_calls = 0
        self.repair_errors = []

    def generate_notes(self):
        return GenerationResult(
            text=self._responses.pop(0),
            finish_reason="STOP",
            input_tokens=1000,
            output_tokens=500,
            thought_tokens=2000,
        )

    def repair(self, latex, errors):
        self.repair_calls += 1
        self.repair_errors.append(errors)
        return self._repairs.pop(0) if self._repairs else latex


def make_settings(tmp_path, **overrides):
    kwargs = dict(url=VALID_URL, out_dir=tmp_path, api_key="fake-key-for-tests")
    kwargs.update(overrides)
    return Settings(**kwargs)


@needs_latex
def test_happy_path_produces_pdf(tmp_path):
    generator = FakeGenerator([GOOD_NOTES])
    result = run(make_settings(tmp_path), generator=generator, verbose=False)

    assert result.ok
    assert result.pdf_path.read_bytes()[:4] == b"%PDF"
    assert result.tex_path.is_file()
    assert "\\documentclass" in result.tex_path.read_text()
    assert "```" not in result.tex_path.read_text()
    assert result.warnings == []
    assert result.repairs == 0
    assert generator.repair_calls == 0
    assert result.generation.thought_tokens == 2000


@needs_latex
def test_repairs_a_broken_document(tmp_path):
    generator = FakeGenerator([BROKEN_NOTES], repairs=[FIXED_NOTES])
    result = run(make_settings(tmp_path), generator=generator, verbose=False)

    assert result.ok, "pipeline should recover after a repair pass"
    assert result.repairs == 1
    assert generator.repair_calls == 1
    # The repair prompt must actually carry the compiler's complaint.
    assert "Undefined control sequence" in generator.repair_errors[0]
    assert "Repaired" in result.tex_path.read_text()


@needs_latex
def test_gives_up_after_configured_repair_attempts(tmp_path):
    generator = FakeGenerator([BROKEN_NOTES], repairs=[BROKEN_NOTES, BROKEN_NOTES])
    settings = make_settings(tmp_path, repair_attempts=2)
    result = run(settings, generator=generator, verbose=False)

    assert not result.ok
    assert generator.repair_calls == 2
    assert result.tex_path.is_file(), "the .tex is kept so the user can fix it"


@needs_latex
def test_missing_package_does_not_burn_repair_calls(tmp_path):
    """A missing .sty is an install problem; rewriting the document cannot fix it."""
    generator = FakeGenerator([MISSING_PKG_NOTES], repairs=[MISSING_PKG_NOTES])
    result = run(make_settings(tmp_path), generator=generator, verbose=False)

    assert not result.ok
    assert generator.repair_calls == 0
    assert any("Missing LaTeX package" in w for w in result.warnings)


@needs_latex
def test_truncated_output_is_closed_and_flagged(tmp_path):
    generator = FakeGenerator([TRUNCATED_NOTES])
    result = run(make_settings(tmp_path), generator=generator, verbose=False)

    assert any("cut off" in w for w in result.warnings)
    source = result.tex_path.read_text()
    assert source.rstrip().endswith("\\end{document}")
    assert "\\end{tcolorbox}" in source
    assert result.ok, "the salvaged document should still compile"


def test_tex_only_skips_compilation(tmp_path):
    generator = FakeGenerator([GOOD_NOTES])
    settings = make_settings(tmp_path, tex_only=True)
    result = run(settings, generator=generator, verbose=False)

    assert result.pdf_path is None
    assert result.compile_result is None
    assert result.tex_path.is_file()


def test_url_is_normalized_before_the_request(tmp_path):
    generator = FakeGenerator([GOOD_NOTES])
    settings = make_settings(tmp_path, url="https://youtu.be/dQw4w9WgXcQ?t=30", tex_only=True)
    run(settings, generator=generator, verbose=False)
    assert settings.url == VALID_URL


def test_save_raw_keeps_the_unprocessed_response(tmp_path):
    generator = FakeGenerator([GOOD_NOTES])
    settings = make_settings(tmp_path, tex_only=True)
    result = run(settings, generator=generator, verbose=False, save_raw=True)
    assert result.raw_path.read_text() == GOOD_NOTES


@needs_latex
def test_aux_files_are_cleaned_by_default(tmp_path):
    run(make_settings(tmp_path), generator=FakeGenerator([GOOD_NOTES]), verbose=False)
    assert not (tmp_path / "notes.aux").exists()
    assert (tmp_path / "notes.pdf").exists()


@needs_latex
def test_no_keep_tex_removes_source_on_success(tmp_path):
    settings = make_settings(tmp_path, keep_tex=False)
    result = run(settings, generator=FakeGenerator([GOOD_NOTES]), verbose=False)
    assert result.ok
    assert not result.tex_path.exists()
