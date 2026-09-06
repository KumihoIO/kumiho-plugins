#!/usr/bin/env python3
"""Approximate token budget for the always-loaded memory policy (kumiho-plugins#97).

The issue proposes a <=1,000-token budget for the main Codex SKILL.md, measured
with a pinned, documented tokenizer. To stay dependency-free and reproducible in
CI, the pinned tokenizer here is a documented deterministic proxy, NOT a specific
model's BPE:

    tokens ~= number of \\w+ runs plus number of individual non-space punctuation
              characters, computed on the body with the YAML frontmatter removed.

This proxy tracks BPE token counts within a stable factor for English + Korean
Markdown and never varies by machine or network. It is a budget gauge, not a
billing figure; when an exact model tokenizer is available it should replace this
and the reported number re-baselined.

Usage:
    python policy_token_budget.py [path ...]        # report
    python policy_token_budget.py --max N [path]    # exit 1 if over N
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FRONTMATTER = re.compile(r"^---\n.*?\n---\n", re.S)
TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)

HERE = Path(__file__).resolve().parent
DEFAULT_TARGET = HERE.parent / "skills" / "kumiho-memory" / "SKILL.md"


def approx_tokens(text: str) -> int:
    """Documented deterministic token proxy (see module docstring)."""
    body = FRONTMATTER.sub("", text, count=1)
    return len(TOKEN.findall(body))


def measure(path: Path) -> int:
    return approx_tokens(path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", type=Path, default=[DEFAULT_TARGET])
    ap.add_argument("--max", type=int, default=None, help="fail if any file exceeds this proxy-token count")
    args = ap.parse_args(argv)
    over = False
    for path in args.paths:
        n = measure(path)
        flag = ""
        if args.max is not None and n > args.max:
            over = True
            flag = f"  OVER budget ({args.max})"
        print(f"{n:6d} proxy-tokens  {path.name}{flag}")
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
