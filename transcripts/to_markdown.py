#!/usr/bin/env python3
"""
Convert a Claude Code session .jsonl into a human-readable Markdown transcript.

    uv run transcripts/to_markdown.py transcripts/claude-code-session.jsonl

Writes a .md next to the input (same name). Renders user prompts, Claude's replies, the tools it
ran (with truncated inputs), and tool results (truncated) — skipping the internal bookkeeping
records. Pass --thinking to also include Claude's (verbose) reasoning blocks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_TOOL_INPUT = 800      # chars; tool inputs (e.g. file writes) truncated past this
MAX_TOOL_RESULT = 700     # chars; tool outputs truncated past this
MAX_THINKING = 1000       # chars; only used with --thinking


def _truncate(s: str, n: int) -> str:
    s = s.rstrip()
    return s if len(s) <= n else s[:n].rstrip() + f'\n… [+{len(s) - n} more chars]'


def _fmt_tool_input(inp: dict) -> str:
    # show a compact, readable version of the tool arguments
    parts = []
    for k, v in inp.items():
        v = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f'{k}: {v}')
    return _truncate('\n'.join(parts), MAX_TOOL_INPUT)


def _render_content(content, include_thinking: bool) -> list[str]:
    out = []
    if isinstance(content, str):
        if content.strip():
            out.append(content.strip())
        return out
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get('type')
        if t == 'text' and b.get('text', '').strip():
            out.append(b['text'].strip())
        elif t == 'thinking' and include_thinking and b.get('thinking', '').strip():
            out.append('> 💭 *(thinking)* ' + _truncate(b['thinking'], MAX_THINKING).replace('\n', '\n> '))
        elif t == 'tool_use':
            out.append(f'🔧 **{b.get("name")}**\n```\n{_fmt_tool_input(b.get("input", {}))}\n```')
        elif t == 'tool_result':
            c = b.get('content')
            if isinstance(c, list):
                c = ' '.join(x.get('text', '[non-text]') for x in c if isinstance(x, dict))
            tag = '⚠️ error' if b.get('is_error') else 'result'
            out.append(f'📄 *{tag}:*\n```\n{_truncate(str(c), MAX_TOOL_RESULT)}\n```')
        elif t == 'image':
            out.append('*[image]*')
    return out


def convert(path: Path, include_thinking: bool) -> Path:
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    title = next((o.get('aiTitle') for o in lines if o.get('type') == 'ai-title'), None)

    md = [f'# {title or "Claude Code session"}', '',
          f'_Human-readable transcript generated from `{path.name}`._', '']

    for o in lines:
        t = o.get('type')
        if t not in ('user', 'assistant') or o.get('isMeta') or o.get('isSidechain'):
            continue
        msg = o.get('message', {})
        blocks = _render_content(msg.get('content'), include_thinking)
        if not blocks:
            continue
        who = '🧑 User' if t == 'user' else '🤖 Claude'
        md.append(f'### {who}')
        md.extend(blocks)
        md.append('')

    out = path.with_suffix('.md')
    out.write_text('\n'.join(md))
    return out


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    inc_think = '--thinking' in sys.argv
    src = Path(args[0]) if args else Path('transcripts/claude-code-session.jsonl')
    dst = convert(src, inc_think)
    print(f'wrote {dst} ({dst.stat().st_size // 1024} KB)'
          + ('  [with thinking]' if inc_think else ''))
