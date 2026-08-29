"""Pull a compilable LaTeX document out of the model's response.

The system instruction asks for bare LaTeX, but models reason before they
answer and often fence their code. This module is deliberately forgiving about
the wrapper and strict about the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

FENCE_RE = re.compile(
    r"```(?:la)?tex[^\n]*\n(.*?)(?:```|\Z)", re.DOTALL | re.IGNORECASE
)
ANY_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)(?:```|\Z)", re.DOTALL)


class NoLatexFound(ValueError):
    """The response contained nothing that looks like a LaTeX document."""


@dataclass
class LatexDocument:
    source: str
    truncated: bool = False

    @property
    def is_complete(self) -> bool:
        return not self.truncated


def extract_latex(response_text: str) -> LatexDocument:
    """Return the LaTeX document contained in *response_text*.

    Handles bare output, ```latex fences, and preamble commentary the model
    was told not to emit but sometimes does.
    """
    if not response_text or not response_text.strip():
        raise NoLatexFound("The model returned an empty response.")

    candidates: List[str] = []

    # Prefer explicitly tagged latex fences, longest first.
    fenced = [m.group(1) for m in FENCE_RE.finditer(response_text)]
    if not fenced:
        fenced = [m.group(1) for m in ANY_FENCE_RE.finditer(response_text)]
    candidates.extend(sorted(fenced, key=len, reverse=True))
    candidates.append(response_text)

    for candidate in candidates:
        doc = _slice_document(candidate)
        if doc is not None:
            return doc

    raise NoLatexFound(
        "No \\documentclass was found in the model's response, so there is no "
        "document to compile. Re-run, or use --save-raw to inspect what came "
        "back."
    )


def _slice_document(text: str) -> Optional[LatexDocument]:
    """Cut *text* down to \\documentclass ... \\end{document} if present."""
    start = text.find("\\documentclass")
    if start == -1:
        return None
    body = text[start:]
    end = body.rfind("\\end{document}")
    if end != -1:
        return LatexDocument(source=body[: end + len("\\end{document}")].strip() + "\n")
    # No \end{document}: the response was cut off. Keep it and flag it so the
    # caller can decide whether to close it or bail.
    return LatexDocument(source=body.strip() + "\n", truncated=True)


def close_truncated_document(source: str) -> str:
    """Best-effort close of a document that was cut off mid-generation.

    Closes environments that are still open, in reverse order, then appends
    \\end{document}. This is a salvage path only; the notes will be incomplete.
    """
    if "\\end{document}" in source:
        return source

    opens = re.findall(r"\\(begin|end)\{([^}]+)\}", source)
    stack: List[str] = []
    for kind, name in opens:
        if kind == "begin":
            stack.append(name)
        elif stack and name in stack:
            # Pop back to the matching begin, discarding anything malformed.
            while stack and stack.pop() != name:
                pass

    tail = []
    # A trailing partial command (e.g. "\\sec") would break the close.
    cleaned = re.sub(r"\\[A-Za-z@]*$", "", source.rstrip())
    for name in reversed(stack):
        if name == "document":
            continue
        tail.append(f"\\end{{{name}}}")
    tail.append("\\end{document}")
    return cleaned + "\n" + "\n".join(tail) + "\n"


def check_document(source: str) -> List[str]:
    """Return non-fatal structural warnings about *source*."""
    warnings: List[str] = []
    if "\\documentclass" not in source:
        warnings.append("missing \\documentclass")
    if "\\begin{document}" not in source:
        warnings.append("missing \\begin{document}")
    if "\\end{document}" not in source:
        warnings.append("missing \\end{document}")

    stack: List[str] = []
    unmatched: List[str] = []
    for kind, name in re.findall(r"\\(begin|end)\{([^}]+)\}", source):
        if kind == "begin":
            stack.append(name)
        else:
            if stack and stack[-1] == name:
                stack.pop()
            elif name in stack:
                while stack and stack.pop() != name:
                    pass
            else:
                unmatched.append(name)
    if stack:
        warnings.append("unclosed environment(s): " + ", ".join(sorted(set(stack))))
    if unmatched:
        warnings.append(
            "unopened \\end for: " + ", ".join(sorted(set(unmatched)))
        )
    return warnings
