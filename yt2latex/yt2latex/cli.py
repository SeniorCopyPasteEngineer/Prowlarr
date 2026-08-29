"""Command line interface."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .compiler import (
    CompileError,
    available_engines,
    cleanup_aux,
    compile_pdf,
    missing_package_hint,
    select_engine,
)
from .config import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_THINKING,
    MEDIA_RESOLUTIONS,
    THINKING_LEVELS,
    ConfigError,
    Settings,
    find_api_key,
    load_dotenv,
)
from .gemini import GeminiError
from .latex import NoLatexFound
from .pipeline import run as run_pipeline
from .youtube import InvalidYouTubeURL

SUBCOMMANDS = ("doctor", "models", "compile")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt2latex",
        description=(
            "Turn a YouTube lecture into rigorous LaTeX study notes and a "
            "compiled PDF, using Gemini 3.7 Flash with high thinking."
        ),
        epilog=(
            "Other commands:\n"
            "  yt2latex doctor            check API key, SDK and LaTeX engine\n"
            "  yt2latex models            list model ids your key can use\n"
            "  yt2latex compile FILE.tex  compile an existing .tex to PDF\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="YouTube URL (watch, youtu.be, shorts, live) or video id")
    parser.add_argument(
        "-o", "--out-dir", default="out", type=Path,
        help="directory for the .tex and .pdf (default: out)",
    )
    parser.add_argument(
        "-n", "--name", default="notes",
        help="base filename without extension (default: notes)",
    )

    model_group = parser.add_argument_group("model")
    model_group.add_argument(
        "-m", "--model", default=os.environ.get("YT2LATEX_MODEL", DEFAULT_MODEL),
        help=f"Gemini model id (default: {DEFAULT_MODEL})",
    )
    model_group.add_argument(
        "-t", "--thinking", default=os.environ.get("YT2LATEX_THINKING", DEFAULT_THINKING),
        choices=THINKING_LEVELS,
        help=f"thinking level / reasoning weight (default: {DEFAULT_THINKING})",
    )
    model_group.add_argument("--temperature", type=float, default=None,
                             help="sampling temperature (default: model default)")
    model_group.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS,
                             help=f"output token ceiling (default: {DEFAULT_MAX_OUTPUT_TOKENS})")
    model_group.add_argument("--max-continuations", type=int, default=3,
                             help="times to resume if output is cut off (default: 3)")
    model_group.add_argument("--api-key", default=None,
                             help="Gemini API key (default: $GEMINI_API_KEY)")
    model_group.add_argument("--timeout", type=int, default=1800,
                             help="per-request timeout in seconds (default: 1800)")

    video_group = parser.add_argument_group("video sampling")
    video_group.add_argument("--start", default=None, metavar="TS",
                             help="start offset, e.g. 90, 1:30 or 1:02:03")
    video_group.add_argument("--end", default=None, metavar="TS",
                             help="end offset, same formats as --start")
    video_group.add_argument("--fps", type=float, default=None,
                             help="frame sampling rate (default: API default of 1)")
    video_group.add_argument("--media-resolution", default="default",
                             choices=MEDIA_RESOLUTIONS,
                             help="video token budget per frame (default: default)")

    latex_group = parser.add_argument_group("latex / pdf")
    latex_group.add_argument("-e", "--engine", default=os.environ.get("YT2LATEX_ENGINE", "auto"),
                             help="latexmk, tectonic, pdflatex, lualatex, xelatex or auto")
    latex_group.add_argument("-r", "--repair", type=int, default=2, metavar="N",
                             help="model repair attempts on compile failure (default: 2)")
    latex_group.add_argument("--compile-timeout", type=int, default=300,
                             help="per-compile timeout in seconds (default: 300)")
    latex_group.add_argument("--tex-only", action="store_true",
                             help="write the .tex and stop, do not compile")
    latex_group.add_argument("--no-keep-tex", action="store_true",
                             help="delete the .tex once the PDF is produced")
    latex_group.add_argument("--keep-aux", action="store_true",
                             help="keep .aux/.toc/.out and friends")
    latex_group.add_argument("--shell-escape", action="store_true",
                             help="allow \\write18 (UNSAFE: the LaTeX is model-written)")

    parser.add_argument("--save-raw", action="store_true",
                        help="also write the model's unprocessed response")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")
    parser.add_argument("--version", action="version", version=f"yt2latex {__version__}")
    return parser


def _bootstrap_env() -> None:
    """Load .env from the CWD and from the project root, if present."""
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def cmd_doctor() -> int:
    print("yt2latex doctor\n")
    ok = True

    try:
        import google.genai  # noqa: F401
        version = getattr(google.genai, "__version__", "unknown")
        print(f"  [ok]   google-genai SDK installed (version {version})")
    except ImportError:
        ok = False
        print("  [FAIL] google-genai not installed -> pip install -r requirements.txt")

    key = find_api_key()
    if key:
        print(f"  [ok]   API key found ({key[:6]}...{key[-4:]}, {len(key)} chars)")
    else:
        ok = False
        print("  [FAIL] no API key. Set GEMINI_API_KEY (see .env.example)")
        print("         Get one at https://aistudio.google.com/apikey")

    engines = available_engines()
    if engines:
        print(f"  [ok]   LaTeX engines: {', '.join(engines)}")
    else:
        ok = False
        print("  [FAIL] no LaTeX engine on PATH (install TeX Live, MacTeX or Tectonic)")

    if shutil.which("kpsewhich"):
        wanted = [
            "amsmath.sty", "amssymb.sty", "tcolorbox.sty", "mdframed.sty",
            "tikz.sty", "tikz-cd.sty", "pgfplots.sty", "algorithm2e.sty",
            "booktabs.sty", "hyperref.sty",
        ]
        import subprocess

        missing = []
        for name in wanted:
            found = subprocess.run(
                ["kpsewhich", name], capture_output=True, text=True
            )
            if not found.stdout.strip():
                missing.append(name)
        if missing:
            print(f"  [warn] packages not found: {', '.join(missing)}")
            print("         The system instruction asks the model to use these.")
            print("         Install texlive-full, or use --engine tectonic.")
        else:
            print("  [ok]   all packages the prompt relies on are installed")

    print("\n" + ("Ready." if ok else "Fix the [FAIL] items above before running."))
    return 0 if ok else 1


def cmd_models(argv) -> int:
    parser = argparse.ArgumentParser(prog="yt2latex models")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args(argv)
    settings = Settings(url="", api_key=args.api_key or find_api_key())
    from .gemini import NotesGenerator

    generator = NotesGenerator(settings, verbose=False)
    for name in generator.list_models():
        marker = " <- default" if name == DEFAULT_MODEL else ""
        print(f"  {name}{marker}")
    return 0


def cmd_compile(argv) -> int:
    parser = argparse.ArgumentParser(prog="yt2latex compile")
    parser.add_argument("tex", type=Path, help="the .tex file to compile")
    parser.add_argument("-o", "--out-dir", type=Path, default=None)
    parser.add_argument("-e", "--engine", default="auto")
    parser.add_argument("--compile-timeout", type=int, default=300)
    parser.add_argument("--keep-aux", action="store_true")
    parser.add_argument("--shell-escape", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    result = compile_pdf(
        args.tex,
        out_dir=args.out_dir,
        engine=args.engine,
        timeout=args.compile_timeout,
        shell_escape=args.shell_escape,
        verbose=not args.quiet,
    )
    if not args.keep_aux and result.log_path:
        cleanup_aux(result.log_path.parent, args.tex.stem)
    if result.ok:
        print(f"PDF: {result.pdf_path}")
        return 0
    print("Compilation failed.\n", file=sys.stderr)
    if result.errors:
        print(result.errors, file=sys.stderr)
    if result.missing_packages:
        print("\n" + missing_package_hint(result.missing_packages), file=sys.stderr)
    if result.log_path:
        print(f"\nFull log: {result.log_path}", file=sys.stderr)
    return 1


def cmd_notes(argv) -> int:
    args = build_parser().parse_args(argv)
    verbose = not args.quiet

    settings = Settings(
        url=args.url,
        out_dir=args.out_dir,
        basename=args.name,
        model=args.model,
        thinking=args.thinking,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        media_resolution=args.media_resolution,
        fps=args.fps,
        start_offset=args.start,
        end_offset=args.end,
        engine=args.engine,
        repair_attempts=args.repair,
        max_continuations=args.max_continuations,
        keep_tex=not args.no_keep_tex,
        keep_aux=args.keep_aux,
        shell_escape=args.shell_escape,
        tex_only=args.tex_only,
        timeout=args.timeout,
        compile_timeout=args.compile_timeout,
        api_key=args.api_key or find_api_key(),
    )
    settings.validate()

    # Fail before spending an API call if the PDF could never be produced.
    if not settings.tex_only:
        select_engine(settings.engine)

    result = run_pipeline(settings, verbose=verbose, save_raw=args.save_raw)

    for warning in result.warnings:
        print(f"\n! {warning}", file=sys.stderr)

    if settings.tex_only:
        print(f"\nLaTeX written to {result.tex_path}")
        return 0

    if result.ok:
        size = result.pdf_path.stat().st_size
        print(f"\nPDF: {result.pdf_path} ({size / 1024:.0f} KB)")
        if result.repairs:
            print(f"     (compiled after {result.repairs} repair pass(es))")
        if settings.keep_tex:
            print(f"TeX: {result.tex_path}")
        print(f"Done in {result.elapsed:.0f}s.")
        return 0

    print("\nCompilation failed after all repair attempts.", file=sys.stderr)
    compiled = result.compile_result
    if compiled and compiled.errors:
        print("\n" + compiled.errors, file=sys.stderr)
    if compiled and compiled.log_path:
        print(f"\nFull log : {compiled.log_path}", file=sys.stderr)
    print(f"LaTeX    : {result.tex_path}", file=sys.stderr)
    print(
        "\nThe .tex has been kept so you can fix it by hand and re-run:\n"
        f"  yt2latex compile {result.tex_path}",
        file=sys.stderr,
    )
    return 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _bootstrap_env()

    if argv and argv[0] in SUBCOMMANDS:
        command, rest = argv[0], argv[1:]
    else:
        command, rest = "notes", argv

    try:
        if command == "doctor":
            return cmd_doctor()
        if command == "models":
            return cmd_models(rest)
        if command == "compile":
            return cmd_compile(rest)
        return cmd_notes(rest)
    except (InvalidYouTubeURL, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (GeminiError, CompileError, NoLatexFound) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
