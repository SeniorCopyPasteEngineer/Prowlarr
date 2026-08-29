# yt2latex

Turn a YouTube lecture into rigorous LaTeX study notes and a compiled PDF.

You give it a YouTube link. It hands the video to **Gemini 3.7 Flash** with
high thinking, under a system instruction that forbids the model from adding
anything the video did not say, takes back the LaTeX, and compiles it to PDF.

```
YouTube URL ──▶ Gemini 3.7 Flash ──▶ LaTeX ──▶ pdflatex ──▶ notes.pdf
                (thinking: high)              (auto-repair on failure)
```

The video is never downloaded. Gemini ingests YouTube URLs natively, so the
request carries a pointer to the video, not the bytes.

## Install

```bash
cd yt2latex
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

You also need a LaTeX toolchain. The system instruction pushes the model
towards `tikz`, `tikz-cd`, `pgfplots`, `tcolorbox` and `algorithm2e`, so a
minimal TeX install will not be enough:

```bash
# Debian / Ubuntu
sudo apt install texlive-latex-extra texlive-pictures texlive-science latexmk

# macOS
brew install --cask mactex

# Any platform, fetches packages on demand
# https://tectonic-typesetting.github.io
```

Then set your API key (get one at <https://aistudio.google.com/apikey>):

```bash
cp .env.example .env    # and edit it
# or: export GEMINI_API_KEY=...
```

Check everything is wired up:

```bash
python -m yt2latex doctor
```

## Use

```bash
python -m yt2latex "https://www.youtube.com/watch?v=VIDEO_ID"
```

Writes `out/notes.tex` and `out/notes.pdf`.

```bash
# Somewhere else, under a different name
python -m yt2latex "https://youtu.be/VIDEO_ID" -o ~/notes -n fourier-lecture

# Only part of a long video
python -m yt2latex URL --start 12:30 --end 48:00

# Cheaper on a long video: fewer frames, smaller frame budget
python -m yt2latex URL --fps 0.2 --media-resolution low

# Stop at the .tex and compile it yourself
python -m yt2latex URL --tex-only

# Compile (or re-compile) a .tex you already have
python -m yt2latex compile out/notes.tex

# What model ids can this key reach?
python -m yt2latex models
```

Installing the package (`pip install -e .`) also puts a `yt2latex` command on
your PATH, so `yt2latex URL` works in place of `python -m yt2latex URL`.

## How it handles the things that go wrong

**The model wraps its output in a code fence,** or narrates before the LaTeX,
despite being told not to. The extractor takes the longest fenced block, or
failing that slices from `\documentclass` to the last `\end{document}`.

**The notes run past the output limit.** Gemini 3.7 Flash caps output at 64k
tokens, and dense notes for a long lecture can hit it. On a `MAX_TOKENS` finish
the tool asks the model to resume and stitches the pieces, dropping any overlap
the model repeats (`--max-continuations`, default 3). If it is still truncated,
the open environments are closed so you get a compilable — but explicitly
flagged as incomplete — document rather than a fragment.

**The LaTeX does not compile.** The compiler errors are fed back to the model
under a *separate, narrower* system instruction that permits syntax fixes only
and freezes the technical content, so a repair pass cannot quietly rewrite your
notes. Two attempts by default (`--repair N`, `0` to disable).

**A package is missing.** That is an installation problem the model cannot fix
by rewriting the document, so the repair loop stops immediately instead of
burning API calls, and tells you what to install.

**The .tex is always kept on failure,** so a run that cannot be repaired still
leaves you something to fix by hand and re-run through `yt2latex compile`.

## Safety note on shell escape

Shell escape is **disabled** on every engine invocation, and the engines run
with `openin_any=p` / `openout_any=p`. The LaTeX being compiled was written by
a language model; `\write18` would let it run arbitrary commands on your
machine. `--shell-escape` exists for the rare package that needs it — read the
generated `.tex` first.

## Options

| Flag | Default | What it does |
| --- | --- | --- |
| `-o, --out-dir` | `out` | Where the `.tex` and `.pdf` go |
| `-n, --name` | `notes` | Base filename |
| `-m, --model` | `gemini-3.7-flash` | Model id |
| `-t, --thinking` | `high` | `minimal`, `low`, `medium`, `high` |
| `--temperature` | model default | Sampling temperature |
| `--max-output-tokens` | `64000` | Output ceiling |
| `--max-continuations` | `3` | Resume attempts when output is cut off |
| `--start` / `--end` | whole video | Offsets: `90`, `1:30`, `1:02:03` |
| `--fps` | API default (1) | Frame sampling rate |
| `--media-resolution` | `default` | `low` / `medium` / `high` token budget per frame |
| `-e, --engine` | `auto` | `latexmk`, `tectonic`, `pdflatex`, `lualatex`, `xelatex` |
| `-r, --repair` | `2` | Model repair attempts on compile failure |
| `--tex-only` | off | Write the `.tex` and stop |
| `--no-keep-tex` | off | Delete the `.tex` once the PDF exists |
| `--keep-aux` | off | Keep `.aux`, `.toc`, `.out`, … |
| `--save-raw` | off | Also write the model's unprocessed response |
| `--shell-escape` | off | Allow `\write18` (unsafe — see above) |

Environment: `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), and optional
`YT2LATEX_MODEL`, `YT2LATEX_THINKING`, `YT2LATEX_ENGINE`. A `.env` file in the
working directory or the project root is loaded automatically; real environment
variables win over it.

## Limits worth knowing

- The video must be **public or unlisted**. Private, members-only and
  age-restricted videos cannot be read by the API.
- The free tier caps how much YouTube video you can process per day, and long
  videos consume a lot of tokens. `--fps` and `--media-resolution low` bring
  the cost down substantially on slide-based lectures.
- One video per request.

## The system instruction

`yt2latex/prompt.py` holds the instruction verbatim. It binds the model to four
axioms — absolute source isolation (no outside knowledge, no gap-filling),
contextual proportion, structural autonomy (no boilerplate template), and
aggressive but epistemically honest visualization. The repair instruction is
kept separate and deliberately narrow so the fix-up pass cannot touch content.

If you change the prompt, change it there; nothing else in the codebase
paraphrases it.

## Layout

```
yt2latex/
├── yt2latex/
│   ├── prompt.py     the system instruction (verbatim) + repair instruction
│   ├── youtube.py    URL shapes -> canonical watch URL
│   ├── gemini.py     the request, continuation stitching, error translation
│   ├── latex.py      pull a compilable document out of the response
│   ├── compiler.py   engine detection, multi-pass compile, log parsing
│   ├── pipeline.py   generate -> extract -> compile -> repair
│   ├── config.py     settings and validation
│   └── cli.py        argument parsing and the subcommands
└── tests/
```

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

The suite compiles real documents through the real toolchain, and asserts the
request we build against the real `google-genai` types, so an API contract
change fails a test rather than a run. Tests needing LaTeX skip themselves when
no engine is installed. No test makes a network call.
