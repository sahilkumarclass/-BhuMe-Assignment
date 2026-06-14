# AI Transcripts

This folder holds the AI conversations used on the BhuMe boundary take-home, as required by the
hand-in (we used AI both to *understand* the problem and to *build* the solution).

## Building the solution (Claude Code CLI)

In this session we: read the problem + starter kit, set up the data layout, wrote `GUIDE.md`, and
built the image-based corrector in `solver/` (edge-response + FFT cross-correlation alignment, a
village-wide drift prior, a truth-free confidence blend, and the correct/flag decision), plus
`solve.py` and `viz.py`.

- **`claude-code-session.md`** — ⭐ **human-readable** transcript (start here). User prompts, Claude's
  replies, the tools it ran, and results, in plain Markdown.
- **`claude-code-session.jsonl`** — the raw session log Claude Code saves locally (machine format).
- **`to_markdown.py`** — the converter that turns the `.jsonl` into the `.md`
  (`uv run transcripts/to_markdown.py transcripts/claude-code-session.jsonl`; add `--thinking` to
  include Claude's reasoning blocks).

> ⚠️ Both files were captured while the session was still active — **regenerate them one last time
> right before submitting** so they cover the whole conversation:
> ```bash
> cp ~/.claude/projects/-Users-sahilkumar-Desktop-bhume-starter-kit/bfd12ecc-dfb3-41da-b77f-5196d72fee96.jsonl \
>    transcripts/claude-code-session.jsonl
> uv run transcripts/to_markdown.py transcripts/claude-code-session.jsonl
> ```

## Understanding the problem (claude.ai web chats)

Paste the **Share** links for any claude.ai conversations used to understand the task here:

- _<paste claude.ai share link>_
- _<paste claude.ai share link>_

(In claude.ai: open the chat → **Share** → copy the public link.)
