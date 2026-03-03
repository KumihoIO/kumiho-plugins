#!/usr/bin/env python3
"""SessionEnd hook — generate a conversation Markdown artifact.

Reads the transcript JSONL file provided by Claude Code or Cowork and
writes a structured Markdown file to the user's artifact directory.
This runs automatically at session end, guaranteeing that every
meaningful session gets a local artifact even if the agent did not
explicitly generate one during the conversation.

Input (JSON on stdin from Claude Code hook system):
    {
      "session_id": "...",
      "transcript_path": "/path/to/transcript.jsonl",
      "cwd": "...",
      "reason": "..."
    }

Output directory resolution order:
    1. KUMIHO_ARTIFACT_DIR environment variable
    2. Agent instruction metadata (artifact_dir from graph — read from
       a local cache file if available)
    3. Default: ~/.kumiho/artifacts/
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _read_hook_input() -> dict:
    """Read the JSON payload from stdin."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def _artifact_dir() -> Path:
    """Resolve the artifact output directory."""
    from_env = (os.getenv("KUMIHO_ARTIFACT_DIR", "") or "").strip()
    if from_env:
        return Path(from_env).expanduser()

    # Check for a local preferences cache written by the plugin
    prefs_path = Path.home() / ".kumiho" / "agent_preferences.json"
    if prefs_path.exists():
        try:
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
            artifact_dir = prefs.get("artifact_dir", "").strip()
            if artifact_dir:
                return Path(artifact_dir).expanduser()
        except Exception:
            pass

    return Path.home() / ".kumiho" / "artifacts"


def _parse_transcript(transcript_path: str) -> list[dict]:
    """Parse a Claude Code transcript JSONL file into exchanges.

    The transcript is a JSONL file where each line is a JSON object.
    We extract user and assistant text messages, skipping tool calls,
    system messages, and other internal entries.
    """
    exchanges: list[dict] = []
    path = Path(transcript_path)
    if not path.exists():
        return exchanges

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Handle different transcript formats
            message = entry.get("message") or entry
            role = message.get("role", "")
            if role not in ("user", "assistant"):
                continue

            content = message.get("content", "")

            # Content can be a string or a list of content blocks
            if isinstance(content, list):
                text_parts: list[str] = []
                for block in content:
                    if isinstance(block, str):
                        text_parts.append(block)
                    elif isinstance(block, dict):
                        block_type = block.get("type", "")
                        if block_type == "text":
                            text_parts.append(block.get("text", ""))
                        elif block_type == "tool_use":
                            tool_name = block.get("name", "unknown")
                            text_parts.append(f"*[Called tool: {tool_name}]*")
                        elif block_type == "tool_result":
                            # Skip tool results — they're verbose
                            continue
                content = "\n".join(text_parts)
            elif not isinstance(content, str):
                continue

            content = content.strip()
            if not content:
                continue

            # Skip system-reminder injections and hook outputs
            if content.startswith("<system-reminder>"):
                continue

            exchanges.append({"role": role, "content": content})
    except Exception:
        pass

    return exchanges


def _extract_topics(exchanges: list[dict]) -> list[str]:
    """Extract rough topic keywords from the conversation."""
    # Simple keyword extraction — not LLM-powered, just frequency-based
    # This is a best-effort heuristic for the frontmatter
    word_freq: dict[str, int] = {}
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "above",
        "below", "between", "and", "but", "or", "not", "no", "nor",
        "so", "yet", "both", "either", "neither", "each", "every",
        "all", "any", "few", "more", "most", "other", "some", "such",
        "than", "too", "very", "just", "about", "also", "then", "that",
        "this", "these", "those", "it", "its", "i", "me", "my", "we",
        "our", "you", "your", "he", "she", "they", "them", "their",
        "what", "which", "who", "whom", "how", "when", "where", "why",
        "if", "because", "while", "although", "though", "since",
        "let", "use", "using", "used", "make", "made", "get", "got",
        "like", "want", "need", "know", "think", "see", "look",
        "here", "there", "now", "well", "way", "even", "new", "one",
        "two", "first", "last", "long", "great", "little", "own",
        "old", "right", "big", "high", "small", "large", "next",
        "early", "young", "important", "public", "bad", "same",
        "able", "sure", "yes", "okay", "ok", "thanks", "thank",
    }

    for ex in exchanges:
        if ex["role"] == "user":
            words = ex["content"].lower().split()
            for w in words:
                w = w.strip(".,;:!?()[]{}\"'`*_#>-/\\")
                if len(w) > 3 and w not in stop_words and w.isalpha():
                    word_freq[w] = word_freq.get(w, 0) + 1

    sorted_words = sorted(word_freq.items(), key=lambda x: -x[1])
    return [w for w, _ in sorted_words[:5]]


def _generate_summary(exchanges: list[dict]) -> str:
    """Generate a one-line summary from the first user message."""
    for ex in exchanges:
        if ex["role"] == "user":
            text = ex["content"][:120].replace("\n", " ").strip()
            if len(ex["content"]) > 120:
                text += "..."
            return text
    return "Session transcript"


def _format_markdown(
    session_id: str,
    exchanges: list[dict],
    now: datetime,
) -> str:
    """Format the conversation as a Markdown document."""
    topics = _extract_topics(exchanges)
    summary = _generate_summary(exchanges)

    lines: list[str] = []

    # YAML frontmatter
    lines.append("---")
    lines.append(f'session_id: "{session_id}"')
    lines.append(f'date: "{now.isoformat()}"')
    if topics:
        lines.append("topics:")
        for t in topics:
            lines.append(f"  - {t}")
    lines.append(f'summary: "{summary}"')
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# Session {session_id[:8]}")
    lines.append("")

    # Exchanges
    exchange_num = 0
    i = 0
    while i < len(exchanges):
        ex = exchanges[i]
        if ex["role"] == "user":
            exchange_num += 1
            lines.append(f"## Exchange {exchange_num}")
            lines.append("")
            lines.append("**User:**")
            lines.append(ex["content"])
            lines.append("")

            # Look ahead for the assistant response
            if i + 1 < len(exchanges) and exchanges[i + 1]["role"] == "assistant":
                lines.append("**Assistant:**")
                lines.append(exchanges[i + 1]["content"])
                lines.append("")
                i += 2
            else:
                i += 1
        elif ex["role"] == "assistant" and exchange_num == 0:
            # Assistant message without a preceding user message
            exchange_num += 1
            lines.append(f"## Exchange {exchange_num}")
            lines.append("")
            lines.append("**Assistant:**")
            lines.append(ex["content"])
            lines.append("")
            i += 1
        else:
            i += 1

    return "\n".join(lines)


def main() -> int:
    hook_input = _read_hook_input()
    session_id = hook_input.get("session_id", "unknown")
    transcript_path = hook_input.get("transcript_path", "")

    if not transcript_path:
        # No transcript available — nothing to do
        return 0

    exchanges = _parse_transcript(transcript_path)

    # Only generate artifacts for meaningful sessions (2+ exchanges)
    if len(exchanges) < 4:  # at least 2 user + 2 assistant messages
        return 0

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    output_dir = _artifact_dir() / date_str
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{session_id}.md"

    # Don't overwrite if the agent already wrote an artifact this session
    if output_path.exists():
        return 0

    markdown = _format_markdown(session_id, exchanges, now)
    output_path.write_text(markdown, encoding="utf-8")

    print(
        f"[kumiho-claude] Session artifact saved: {output_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
