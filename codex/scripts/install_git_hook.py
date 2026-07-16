#!/usr/bin/env python3
"""Install the Decision Memory auto-capture git hook into a repository.

Codex CLI has no lifecycle-hook system, so capture rides on git itself:
a ``post-commit`` hook spawns the shared detached ingest worker, which
mines the new commit into decision nodes (incremental — already-captured
commits are marker-skipped at zero LLM cost).  The hook backgrounds the
worker and always exits 0: committing is never blocked or slowed.

This is editor-agnostic — the same hook serves Codex, plain terminals,
or any other tool that commits.

Usage::

    python codex/scripts/install_git_hook.py [/path/to/repo]   # default: cwd
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

MARKER = "# kumiho-decision-memory-capture"

_WORKER = (
    Path(__file__).resolve().parent.parent.parent
    / "claude" / "scripts" / "code_ingest_worker.py"
)


def main() -> int:
    if not _WORKER.exists():
        print(
            "[kumiho-codex] Ingest worker not found at "
            f"{_WORKER} — the git hook requires a full kumiho-plugins "
            "checkout (plugin snapshots do not include claude/). Clone the "
            "repo and run this installer from there, or rely on "
            "kumiho_code_capture from the agent instead.",
            file=sys.stderr,
        )
        return 1
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    probe = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-dir"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        print(f"[kumiho-codex] {repo} is not a git repository.", file=sys.stderr)
        return 1
    git_dir = Path(probe.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    worker = str(_WORKER).replace("\\", "/")
    python = sys.executable.replace("\\", "/")
    snippet = (
        f"{MARKER}\n"
        f'"{python}" "{worker}" "$(git rev-parse --show-toplevel)" '
        f">/dev/null 2>&1 &\n"
    )

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8")
        if MARKER in existing:
            print(f"[kumiho-codex] {hook_path} already has the capture hook.")
            return 0
        content = existing.rstrip("\n") + "\n\n" + snippet
    else:
        content = "#!/bin/sh\n" + snippet

    hook_path.write_text(content, encoding="utf-8", newline="\n")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[kumiho-codex] Installed decision auto-capture hook: {hook_path}")
    print("[kumiho-codex] Commits in this repo will now mine decisions "
          "into the graph (incremental, detached, never blocks the commit).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
