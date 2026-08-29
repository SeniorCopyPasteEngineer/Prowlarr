import pytest

from yt2latex.compiler import (
    CompileError,
    NoEngineFound,
    available_engines,
    cleanup_aux,
    compile_pdf,
    find_missing_packages,
    missing_package_hint,
    parse_errors,
    select_engine,
)

needs_latex = pytest.mark.skipif(
    not available_engines(), reason="no LaTeX engine installed"
)

GOOD = r"""
\documentclass[11pt]{article}
\usepackage{amsmath,amssymb}
\usepackage{tcolorbox}
\tcbuselibrary{most}
\usepackage{tikz}
\usetikzlibrary{arrows.meta,positioning}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usepackage[ruled,vlined]{algorithm2e}
\begin{document}
\section{Gradient Descent}
\begin{tcolorbox}[colback=blue!5,title=Update Rule]
$x_{k+1} = x_k - \alpha \nabla f(x_k)$
\end{tcolorbox}
\begin{tikzpicture}[>=Stealth]
\node (a) {$x_k$}; \node (b) [right=2cm of a] {$x_{k+1}$};
\draw[->] (a) -- (b);
\end{tikzpicture}
\begin{tikzpicture}
\begin{axis}[width=6cm,height=4cm]\addplot {x^2};\end{axis}
\end{tikzpicture}
\begin{algorithm}[H]
\KwIn{$x_0$, $\alpha$}
\For{$k=1$ \KwTo $N$}{$x_k \leftarrow x_{k-1}$\;}
\caption{Descent}
\end{algorithm}
\end{document}
"""

BROKEN = r"""
\documentclass{article}
\begin{document}
\section{Broken}
\thisCommandDoesNotExist{x}
\end{document}
"""

SAMPLE_LOG = r"""
This is pdfTeX, Version 3.141592653
! Undefined control sequence.
l.5 \thisCommandDoesNotExist
                            {x}
The control sequence at the end of the top line
! LaTeX Error: File `nonexistentpkg.sty' not found.
Type X to quit or <RETURN> to proceed.
l.3 \usepackage{nonexistentpkg}
"""


def test_parse_errors_captures_blocks_and_line_pointers():
    errors = parse_errors(SAMPLE_LOG)
    assert "Undefined control sequence" in errors
    assert "l.5 \\thisCommandDoesNotExist" in errors
    assert "nonexistentpkg.sty" in errors
    assert "This is pdfTeX" not in errors


def test_parse_errors_deduplicates_repeated_blocks():
    errors = parse_errors("! Undefined control sequence.\n" * 50)
    assert errors.count("Undefined control sequence") == 1


def test_parse_errors_truncates_long_output():
    log = "\n".join(f"! LaTeX Error: problem number {n} here." for n in range(2000))
    errors = parse_errors(log, max_chars=500)
    assert len(errors) < 700
    assert "truncated" in errors


def test_parse_errors_empty_log():
    assert parse_errors("") == ""
    assert parse_errors("all fine here\nno problems") == ""


def test_find_missing_packages():
    assert find_missing_packages(SAMPLE_LOG) == ["nonexistentpkg.sty"]
    assert find_missing_packages("nothing wrong") == []


def test_missing_package_hint_mentions_tectonic():
    hint = missing_package_hint(["tcolorbox.sty"])
    assert "tcolorbox.sty" in hint and "tectonic" in hint.lower()


def test_select_engine_rejects_unknown_engine():
    with pytest.raises(NoEngineFound):
        select_engine("definitely-not-an-engine")


def test_compile_missing_file_raises():
    with pytest.raises(CompileError):
        compile_pdf("does-not-exist.tex")


@needs_latex
def test_compiles_document_with_all_prompt_packages(tmp_path):
    tex = tmp_path / "good.tex"
    tex.write_text(GOOD, encoding="utf-8")
    result = compile_pdf(tex, out_dir=tmp_path, verbose=False)
    assert result.ok, result.errors
    assert result.pdf_path.is_file()
    assert result.pdf_path.read_bytes()[:4] == b"%PDF"


@needs_latex
def test_broken_document_reports_errors(tmp_path):
    tex = tmp_path / "bad.tex"
    tex.write_text(BROKEN, encoding="utf-8")
    result = compile_pdf(tex, out_dir=tmp_path, verbose=False)
    assert not result.ok
    assert result.pdf_path is None
    assert "Undefined control sequence" in result.errors


@needs_latex
def test_stale_pdf_is_not_reported_as_success(tmp_path):
    tex = tmp_path / "bad.tex"
    tex.write_text(BROKEN, encoding="utf-8")
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.5 stale")
    result = compile_pdf(tex, out_dir=tmp_path, verbose=False)
    assert not result.ok


@needs_latex
def test_cleanup_aux_keeps_tex_pdf_log(tmp_path):
    tex = tmp_path / "good.tex"
    tex.write_text(GOOD, encoding="utf-8")
    compile_pdf(tex, out_dir=tmp_path, verbose=False)
    cleanup_aux(tmp_path, "good")
    remaining = {p.suffix for p in tmp_path.iterdir()}
    assert ".aux" not in remaining
    assert {".tex", ".pdf"} <= remaining


@needs_latex
def test_shell_escape_is_off_by_default(tmp_path):
    """\\write18 must not run commands from model-written LaTeX."""
    tex = tmp_path / "escape.tex"
    marker = tmp_path / "pwned.txt"
    tex.write_text(
        "\\documentclass{article}\\begin{document}\n"
        f"\\immediate\\write18{{touch {marker}}}\n"
        "ok\\end{document}\n",
        encoding="utf-8",
    )
    compile_pdf(tex, out_dir=tmp_path, verbose=False)
    assert not marker.exists(), "shell escape executed with default settings"
