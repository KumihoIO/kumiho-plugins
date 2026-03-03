#!/usr/bin/env python3
"""Auto-approve non-destructive Kumiho memory tool calls.

Destructive operations (delete, untag, deprecate) still require
manual user approval via the normal permission dialog.

Replaces auto-approve-memory.sh for cross-platform compatibility
(the bash version depended on jq which is unavailable on Windows).
"""

import json
import sys


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        # Can't parse input — fall through to manual approval
        return

    tool_name = data.get("tool_name", "")

    # Let destructive operations fall through to the permission dialog
    destructive_keywords = ("delete", "untag", "deprecate")
    if any(kw in tool_name.lower() for kw in destructive_keywords):
        return

    # Auto-approve everything else from kumiho-memory
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
