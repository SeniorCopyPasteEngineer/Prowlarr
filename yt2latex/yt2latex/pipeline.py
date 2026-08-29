"""End-to-end orchestration: YouTube URL -> LaTeX -> PDF."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .compiler import (
    CompileResult,
    cleanup_aux,
    compile_pdf,
    missing_package_hint,
)
from .config import Settings
from .gemini import GenerationResult, NotesGenerator
from .latex import check_document, close_truncated_document, extract_latex
from .youtube import normalize_url


@dataclass
class PipelineResult:
    tex_path: Path
    pdf_path: Optional[Path] = None
    log_path: Optional[Path] = None
    raw_path: Optional[Path] = None
    repairs: int = 0
    generation: Optional[GenerationResult] = None
    compile_result: Optional[CompileResult] = None
    warnings: List[str] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.pdf_path is not None and self.pdf_path.is_file()


def run(
    settings: Settings,
    generator: Optional[NotesGenerator] = None,
    verbose: bool = True,
    save_raw: bool = False,
) -> PipelineResult:
    """Generate notes for ``settings.url`` and compile them to a PDF.

    *generator* is injectable so the pipeline can be tested without hitting
    the API.
    """
    started = time.monotonic()

    def say(message: str) -> None:
        if verbose:
            print(message, flush=True)

    settings.url = normalize_url(settings.url)
    settings.validate()
    out_dir = Path(settings.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / f"{settings.basename}.tex"

    if generator is None:
        generator = NotesGenerator(settings, verbose=verbose)

    say(f"→ Video   : {settings.url}")
    say(f"→ Model   : {settings.model} (thinking: {settings.thinking})")
    say("→ Analysing video and writing LaTeX (this takes a few minutes)...")

    generation = generator.generate_notes()
    document = extract_latex(generation.text)

    result = PipelineResult(tex_path=tex_path, generation=generation)

    if save_raw:
        raw_path = out_dir / f"{settings.basename}.raw.txt"
        raw_path.write_text(generation.text, encoding="utf-8")
        result.raw_path = raw_path

    latex = document.source
    if document.truncated:
        result.warnings.append(
            "The model's output was cut off before \\end{document}; the "
            "document was closed automatically and the notes are incomplete. "
            "Raise --max-continuations or narrow the range with --start/--end."
        )
        latex = close_truncated_document(latex)

    for warning in check_document(latex):
        result.warnings.append(f"structural warning: {warning}")

    tex_path.write_text(latex, encoding="utf-8")
    say(f"→ LaTeX   : {tex_path} ({len(latex):,} chars)")
    if generation.total_tokens:
        say(
            f"→ Tokens  : {generation.input_tokens:,} in / "
            f"{generation.output_tokens:,} out / "
            f"{generation.thought_tokens:,} thinking"
        )

    if settings.tex_only:
        result.elapsed = time.monotonic() - started
        return result

    compiled = compile_pdf(
        tex_path,
        out_dir=out_dir,
        engine=settings.engine,
        timeout=settings.compile_timeout,
        shell_escape=settings.shell_escape,
        verbose=verbose,
    )
    result.compile_result = compiled

    attempt = 0
    while not compiled.ok and attempt < settings.repair_attempts:
        if compiled.missing_packages:
            # A missing .sty is an installation problem; the model cannot fix
            # it by rewriting the document, so stop burning API calls on it.
            result.warnings.append(missing_package_hint(compiled.missing_packages))
            break
        if not compiled.errors:
            break
        attempt += 1
        say(
            f"→ Compile failed; asking the model to repair "
            f"({attempt}/{settings.repair_attempts})..."
        )
        repaired_text = generator.repair(latex, compiled.errors)
        try:
            repaired = extract_latex(repaired_text)
        except ValueError:
            result.warnings.append("Repair pass returned no usable LaTeX.")
            break
        latex = repaired.source
        if repaired.truncated:
            latex = close_truncated_document(latex)
        tex_path.write_text(latex, encoding="utf-8")
        compiled = compile_pdf(
            tex_path,
            out_dir=out_dir,
            engine=settings.engine,
            timeout=settings.compile_timeout,
            shell_escape=settings.shell_escape,
            verbose=verbose,
        )
        result.compile_result = compiled

    result.repairs = attempt
    if compiled.ok:
        result.pdf_path = compiled.pdf_path
    result.log_path = compiled.log_path

    if not settings.keep_aux:
        cleanup_aux(out_dir, settings.basename)
    if not settings.keep_tex and result.ok:
        tex_path.unlink(missing_ok=True)

    result.elapsed = time.monotonic() - started
    return result
