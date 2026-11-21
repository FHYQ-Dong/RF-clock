"""Flag the mechanical faults the user has corrected in reports, before the user sees them.

- a colon or semicolon in prose (times of day, ratios, URLs, drive letters, code and link targets excepted)
- a paragraph or list item broken over several lines (Markdown here is not hard-wrapped)
- a link or image whose target file does not exist
- a Chinese file (*_zh.md) without its English file beside it, or the other way round

    python .claude/skills/write-report/scripts/check_report.py <file.md> [<file.md> ...]

Exit status 1 when anything is flagged. The content checks (every number defined, every term explained,
one quantity per figure panel) are the skill's checklist and need reading, not this script.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NOTATION = re.compile(r"\d\s?:\s?\d|https?://\S+|\b[A-Za-z]:[\\/]")  # 20:15, 1:1, URLs, D:\ paths
BLOCK_START = re.compile(r"\s*([-*+]\s|\d+\.\s|\||#|!\[|>|```)")


def prose(line: str) -> str:
    line = re.sub(r"`[^`]*`", "", line)            # inline code
    line = re.sub(r"\]\([^)]*\)", "]", line)       # link targets
    return NOTATION.sub("", line)


def check(path: Path) -> list[str]:
    out = []
    lines = path.read_text(encoding="utf-8").splitlines()
    in_code = False
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not s:
            continue
        for target in re.findall(r"\]\(([^)\s]+)\)", line):
            if re.match(r"https?:|#|mailto:", target):
                continue
            if not (path.parent / target.split("#")[0]).exists():
                out.append(f"{path}:{n}: link target missing: {target}")
        if not s.startswith(("![", "|---", "| ---")) and re.search(r"[:;：；]", prose(line)):
            out.append(f"{path}:{n}: colon or semicolon in prose: {s[:70]}")
        nxt = lines[n] if n < len(lines) else ""
        if (not s.startswith(("|", "#", "![")) and nxt.strip()
                and not BLOCK_START.match(nxt) and not nxt.startswith("    ")):
            out.append(f"{path}:{n}: hard-wrapped, the next line continues it: {nxt.strip()[:50]}")
    pair = path.with_name(path.name.replace("_zh.md", ".md")) if path.name.endswith("_zh.md") \
        else path.with_name(path.stem + "_zh.md")
    if not pair.exists():
        out.append(f"{path}: no {pair.name} beside it (English is the reference, Chinese its translation)")
    return out


def main() -> None:
    problems = [p for f in sys.argv[1:] for p in check(Path(f))]
    print("\n".join(problems) or "nothing flagged")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
