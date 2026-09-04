#!/usr/bin/env python3
"""Read-only backend verification for Codex onboarding."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ingest_skills import _configure_backend, _load_config


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the configured Kumiho backend.")
    parser.add_argument("--backend", required=True, choices=("cloud", "ce"))
    parser.add_argument("--config", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = _load_config(args.config, args.backend)
        _configure_backend(args.backend, config)
        import kumiho

        # Read-only and valid for an empty tenant: this proves discovery plus
        # the backend's gRPC path, not merely that Python modules import.
        kumiho.get_client().get_projects()
    except Exception as exc:
        # SDK transport exceptions can carry request metadata. Report only the
        # class, never raw bodies, headers, endpoints, or environment values.
        print(
            f"[kumiho-codex] Backend verification failed ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1

    print(f"[kumiho-codex] {args.backend.upper()} backend verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
