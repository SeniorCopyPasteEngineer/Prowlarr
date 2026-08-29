"""Compile LaTeX to PDF and make sense of the failures.

Shell escape is disabled on every engine invocation: the source being compiled
was written by a language model, and \\write18 would let it run arbitrary
commands on the user's machine. Pass --shell-escape only if you have read the
generated .tex yourself.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

# Ordered by preference. latexmk drives the rerun logic for us; tectonic is next
# because it fetches missing packages on demand.
ENGINES = ("latexmk", "tectonic", "pdflatex", "lualatex", "xelatex")

MAX_PASSES = 4
RERUN_RE = re.compile(
    r"(Rerun to get|Rerun LaTeX|There were undefined references|"
    r"Label\(s\) may have changed)",
    re.IGNORECASE,
)
MISSING_FILE_RE = re.compile(r"File [`']([^']+)' not found")
MISSING_PKG_RE = re.compile(r"(?:LaTeX Error: )?File [`']([\w@.-]+)\.(sty|cls)' not found")


class CompileError(RuntimeError):
    """Compilation failed for a reason the caller should report."""


class NoEngineFound(CompileError):
    """No LaTeX engine is installed."""


@dataclass
class CompileResult:
    ok: bool
    pdf_path: Optional[Path]
    log_path: Optional[Path]
    engine: str
    passes: int = 0
    errors: str = ""
    missing_packages: List[str] = field(default_factory=list)
    log_text: str = ""


def available_engines() -> List[str]:
    return [name for name in ENGINES if shutil.which(name)]


def select_engine(preference: str = "auto") -> str:
    """Return the engine to use, or raise if it is not usable."""
    found = available_engines()
    if preference and preference != "auto":
        if shutil.which(preference):
            return preference
        raise NoEngineFound(
            f"Requested engine {preference!r} is not on PATH."
            + (f" Available: {', '.join(found)}." if found else "")
        )
    if not found:
        raise NoEngineFound(
            "No LaTeX engine found on PATH. Install one of:\n"
            "  Debian/Ubuntu : sudo apt install texlive-full latexmk\n"
            "                  (or texlive-latex-extra texlive-pictures\n"
            "                   texlive-science for a smaller install)\n"
            "  macOS         : brew install --cask mactex   (or basictex)\n"
            "  Any platform  : install Tectonic, which fetches packages on\n"
            "                  demand -> https://tectonic-typesetting.github.io\n"
            "\nOr run with --tex-only to produce the .tex and compile elsewhere."
        )
    return found[0]


def _command(
    engine: str, tex_path: Path, out_dir: Path, shell_escape: bool
) -> Sequence[str]:
    """Build the engine command line.

    ``tex_path`` is reduced to its bare filename: the engine runs with cwd set
    to the document's directory, and openin_any=p (see ``compile_pdf``) refuses
    to read absolute paths -- including the main input file itself.
    """
    escape = "--shell-escape" if shell_escape else "--no-shell-escape"
    tex_name = tex_path.name
    if engine == "latexmk":
        return [
            "latexmk",
            "-pdf",
            "-interaction=nonstopmode",
            "-file-line-error",
            "-halt-on-error",
            escape,
            f"-outdir={out_dir}",
            tex_name,
        ]
    if engine == "tectonic":
        cmd = [
            "tectonic",
            "-X",
            "compile",
            tex_name,
            "--outdir",
            str(out_dir),
            "--keep-logs",
        ]
        if shell_escape:
            cmd.extend(["-Z", "shell-escape"])
        return cmd
    return [
        engine,
        "-interaction=nonstopmode",
        "-file-line-error",
        escape,
        f"-output-directory={out_dir}",
        tex_name,
    ]


def _read_log(log_path: Path) -> str:
    try:
        return log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def compile_pdf(
    tex_path: Path,
    out_dir: Optional[Path] = None,
    engine: str = "auto",
    timeout: int = 300,
    shell_escape: bool = False,
    verbose: bool = True,
) -> CompileResult:
    """Compile *tex_path*, running as many passes as the document needs."""
    tex_path = Path(tex_path).resolve()
    if not tex_path.is_file():
        raise CompileError(f"No such file: {tex_path}")
    out_dir = Path(out_dir or tex_path.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    engine = select_engine(engine)
    stem = tex_path.stem
    pdf_path = out_dir / f"{stem}.pdf"
    log_path = out_dir / f"{stem}.log"

    # A stale PDF from a previous run must not be mistaken for success.
    if pdf_path.exists():
        pdf_path.unlink()

    env = dict(os.environ)
    env.setdefault("max_print_line", "1000")
    env.setdefault("openout_any", "p")
    env.setdefault("openin_any", "p")
    env.setdefault("SOURCE_DATE_EPOCH", "0")

    cmd = _command(engine, tex_path, out_dir, shell_escape)
    # latexmk and tectonic handle their own rerun logic; the raw engines do not.
    passes = 1 if engine in ("latexmk", "tectonic") else MAX_PASSES
    stdout = ""
    completed = None

    for index in range(passes):
        if verbose:
            label = f"pass {index + 1}/{passes}" if passes > 1 else "compiling"
            print(f"  {engine}: {label}...", flush=True)
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(tex_path.parent),
                env=env,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise CompileError(
                f"{engine} timed out after {timeout}s. A runaway TikZ or "
                "pgfplots picture is the usual cause; raise --compile-timeout "
                "or inspect the .tex."
            ) from None
        except OSError as exc:
            raise CompileError(f"Could not run {engine}: {exc}") from exc

        stdout = (completed.stdout or "") + (completed.stderr or "")
        log_text = _read_log(log_path) or stdout

        if completed.returncode != 0:
            break
        if engine not in ("latexmk", "tectonic") and not RERUN_RE.search(log_text):
            break

    log_text = _read_log(log_path) or stdout
    errors = parse_errors(log_text) or parse_errors(stdout)
    missing = find_missing_packages(log_text + "\n" + stdout)
    succeeded = (
        completed is not None
        and completed.returncode == 0
        and pdf_path.is_file()
        and pdf_path.stat().st_size > 0
    )

    return CompileResult(
        ok=succeeded,
        pdf_path=pdf_path if succeeded else None,
        log_path=log_path if log_path.is_file() else None,
        engine=engine,
        passes=(index + 1) if completed is not None else 0,
        errors=errors,
        missing_packages=missing,
        log_text=log_text,
    )


def parse_errors(log_text: str, max_chars: int = 6000) -> str:
    """Extract the TeX error blocks from a log, small enough to send back.

    Keeps each ``!`` error with its trailing context and the ``l.<n>`` line
    pointer, which together are what a repair pass actually needs.
    """
    if not log_text:
        return ""
    lines = log_text.splitlines()
    blocks: List[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        is_error = line.startswith("!") or re.match(r"^[^\s:]+:\d+: ", line)
        if is_error:
            block = [line]
            cursor = index + 1
            # Grab context up to and including the l.<n> pointer.
            while cursor < len(lines) and cursor - index <= 12:
                nxt = lines[cursor]
                block.append(nxt)
                if nxt.startswith("l."):
                    # The line after l.<n> holds the rest of the offending line.
                    if cursor + 1 < len(lines):
                        block.append(lines[cursor + 1])
                    break
                if nxt.startswith("!"):
                    # A new error begins here; it is not context for this one.
                    block.pop()
                    break
                cursor += 1
            blocks.append("\n".join(b.rstrip() for b in block if b.strip()))
            index = cursor + 1
        else:
            index += 1

    seen = set()
    unique = []
    for block in blocks:
        key = block[:200]
        if key not in seen:
            seen.add(key)
            unique.append(block)

    text = "\n\n".join(unique)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[...errors truncated...]"
    return text.strip()


def find_missing_packages(log_text: str) -> List[str]:
    """Return names of .sty/.cls files the engine could not find."""
    names = {f"{m.group(1)}.{m.group(2)}" for m in MISSING_PKG_RE.finditer(log_text)}
    if not names:
        names = {
            m.group(1)
            for m in MISSING_FILE_RE.finditer(log_text)
            if m.group(1).endswith((".sty", ".cls"))
        }
    return sorted(names)


def missing_package_hint(missing: Sequence[str]) -> str:
    if not missing:
        return ""
    return (
        "Missing LaTeX package file(s): "
        + ", ".join(missing)
        + "\nYour TeX installation is incomplete. Install the full distribution\n"
        "  Debian/Ubuntu : sudo apt install texlive-full\n"
        "  macOS         : brew install --cask mactex\n"
        "or use Tectonic (--engine tectonic), which downloads packages on demand."
    )


def cleanup_aux(out_dir: Path, stem: str) -> None:
    """Remove the usual TeX by-products, keeping .tex, .pdf and .log."""
    suffixes = (
        ".aux", ".toc", ".out", ".lof", ".lot", ".fls", ".fdb_latexmk",
        ".synctex.gz", ".nav", ".snm", ".vrb", ".bbl", ".blg", ".idx",
        ".ilg", ".ind", ".auxlock", ".run.xml",
    )
    for suffix in suffixes:
        candidate = out_dir / f"{stem}{suffix}"
        if candidate.exists():
            try:
                candidate.unlink()
            except OSError:
                pass
