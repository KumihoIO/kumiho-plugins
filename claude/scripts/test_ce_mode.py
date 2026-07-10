#!/usr/bin/env python3
"""Offline unit checks for self-hosted CE (Community Edition) mode.

Exercises the CE-mode branch of the launcher without any network or venv:
detection, endpoint resolution, SDK env wiring, LLM-endpoint handling, and that
the cloud fail-fast default is preserved when CE mode is off.

Usage (from kumiho-claude/ or repo root):
    python kumiho-claude/scripts/test_ce_mode.py
"""

from __future__ import annotations

import os
import sys
import tempfile

import run_kumiho_mcp as bootstrap


# Every env var the CE / cloud / LLM paths read or write, cleared between cases.
_MANAGED_KEYS = (
    "KUMIHO_CLAUDE_MODE",
    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
    "KUMIHO_LOCAL_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ADDRESS",
    "KUMIHO_AUTH_TOKEN",
    "KUMIHO_TENANT_HINT",
    "KUMIHO_CONTROL_PLANE_URL",
    "UPSTASH_REDIS_URL",
    "KUMIHO_LLM_BASE_URL",
    "KUMIHO_LLM_PROVIDER",
    "KUMIHO_LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK",
)


def _reset_env() -> None:
    for key in _MANAGED_KEYS:
        os.environ.pop(key, None)


def _check(name: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    line = f"[{status}] {name}"
    if detail and not condition:
        line += f" -- {detail}"
    print(line, file=sys.stderr if not condition else sys.stdout)
    return condition


def test_ce_mode_detection() -> bool:
    ok = True

    _reset_env()
    ok &= _check("mode off by default", bootstrap._ce_mode_enabled() is False)

    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    ok &= _check("KUMIHO_CLAUDE_MODE=ce enables", bootstrap._ce_mode_enabled() is True)

    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "CE"
    ok &= _check("mode is case-insensitive", bootstrap._ce_mode_enabled() is True)

    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "self-hosted"
    ok &= _check("mode=self-hosted enables", bootstrap._ce_mode_enabled() is True)

    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "cloud"
    ok &= _check("mode=cloud stays off", bootstrap._ce_mode_enabled() is False)

    _reset_env()
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "127.0.0.1:9190"
    ok &= _check("explicit endpoint implies CE", bootstrap._ce_mode_enabled() is True)

    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "${KUMIHO_CLAUDE_MODE:-}"
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "${KUMIHO_CLAUDE_SERVER_ENDPOINT:-}"
    ok &= _check("unresolved placeholders stay off", bootstrap._ce_mode_enabled() is False)

    return ok


def test_endpoint_resolution() -> bool:
    ok = True

    _reset_env()
    ok &= _check("resolve None when off", bootstrap._resolve_ce_endpoint() is None)

    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    resolved = bootstrap._resolve_ce_endpoint()
    ok &= _check(
        "default endpoint when mode-only",
        resolved == bootstrap.DEFAULT_CE_ENDPOINT,
        f"got {resolved!r}",
    )

    _reset_env()
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "grpc://myhost:9200"
    resolved = bootstrap._resolve_ce_endpoint()
    ok &= _check("scheme URL normalized", resolved == "myhost:9200", f"got {resolved!r}")

    _reset_env()
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "127.0.0.1:9190"
    resolved = bootstrap._resolve_ce_endpoint()
    ok &= _check("host:port passthrough", resolved == "127.0.0.1:9190", f"got {resolved!r}")

    return ok


def test_ce_env_wiring() -> bool:
    ok = True

    # A stray cloud token + endpoint must be neutralized in CE mode.
    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["KUMIHO_AUTH_TOKEN"] = "eyJstray.cloud.token"
    os.environ["KUMIHO_SERVER_ENDPOINT"] = "us-central.kumiho.cloud:443"
    bootstrap._bootstrap_server_endpoint()
    ok &= _check(
        "LOCAL endpoint set to CE default",
        os.environ.get("KUMIHO_LOCAL_SERVER_ENDPOINT") == bootstrap.DEFAULT_CE_ENDPOINT,
        f"got {os.environ.get('KUMIHO_LOCAL_SERVER_ENDPOINT')!r}",
    )
    ok &= _check(
        "cloud endpoint cleared (no .invalid sentinel)",
        "KUMIHO_SERVER_ENDPOINT" not in os.environ,
        f"got {os.environ.get('KUMIHO_SERVER_ENDPOINT')!r}",
    )
    ok &= _check(
        "token blanked to force tokenless CE",
        os.environ.get("KUMIHO_AUTH_TOKEN") == "",
        f"got {os.environ.get('KUMIHO_AUTH_TOKEN')!r}",
    )
    ok &= _check(
        "default Redis URL supplied",
        os.environ.get("UPSTASH_REDIS_URL") == bootstrap.DEFAULT_CE_REDIS_URL,
        f"got {os.environ.get('UPSTASH_REDIS_URL')!r}",
    )

    # A user-provided Redis URL must win over the default.
    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["UPSTASH_REDIS_URL"] = "redis://10.0.0.5:6380"
    bootstrap._bootstrap_server_endpoint()
    ok &= _check(
        "user Redis URL preserved",
        os.environ.get("UPSTASH_REDIS_URL") == "redis://10.0.0.5:6380",
        f"got {os.environ.get('UPSTASH_REDIS_URL')!r}",
    )

    return ok


def test_cloud_default_preserved() -> bool:
    # No CE env, no token: the tokenless discovery-skip branch must still pin the
    # unreachable sentinel so the SDK cannot silently attach to localhost:8080.
    ok = True
    _reset_env()
    bootstrap._bootstrap_server_endpoint()
    ok &= _check(
        "cloud tokenless keeps .invalid sentinel",
        os.environ.get("KUMIHO_SERVER_ENDPOINT") == "needs-auth.kumiho.invalid:443",
        f"got {os.environ.get('KUMIHO_SERVER_ENDPOINT')!r}",
    )
    ok &= _check(
        "cloud path sets no LOCAL endpoint",
        "KUMIHO_LOCAL_SERVER_ENDPOINT" not in os.environ,
        f"got {os.environ.get('KUMIHO_LOCAL_SERVER_ENDPOINT')!r}",
    )
    return ok


def test_llm_fallback() -> bool:
    ok = True

    # No key, no base URL -> dead-port fail-fast fallback.
    _reset_env()
    bootstrap._configure_llm_fallback()
    ok &= _check(
        "no-config uses dead-port fallback",
        os.environ.get("KUMIHO_LLM_BASE_URL") == "http://127.0.0.1:9/v1"
        and os.environ.get("OPENAI_API_KEY") == "kumiho-claude-fallback",
        f"base={os.environ.get('KUMIHO_LLM_BASE_URL')!r} key={os.environ.get('OPENAI_API_KEY')!r}",
    )

    # Self-provided local LLM base URL, no key -> honored, not clobbered.
    _reset_env()
    os.environ["KUMIHO_LLM_BASE_URL"] = "http://127.0.0.1:11434/v1"
    bootstrap._configure_llm_fallback()
    ok &= _check(
        "self-provided base URL preserved",
        os.environ.get("KUMIHO_LLM_BASE_URL") == "http://127.0.0.1:11434/v1",
        f"got {os.environ.get('KUMIHO_LLM_BASE_URL')!r}",
    )
    ok &= _check(
        "dummy key set for keyless local LLM",
        os.environ.get("OPENAI_API_KEY") == "kumiho-local-llm",
        f"got {os.environ.get('OPENAI_API_KEY')!r}",
    )

    # Real key present -> function is a no-op (no base URL injected).
    _reset_env()
    os.environ["OPENAI_API_KEY"] = "sk-real-key"
    bootstrap._configure_llm_fallback()
    ok &= _check(
        "real key left untouched",
        os.environ.get("OPENAI_API_KEY") == "sk-real-key"
        and "KUMIHO_LLM_BASE_URL" not in os.environ,
        f"key={os.environ.get('OPENAI_API_KEY')!r} base={os.environ.get('KUMIHO_LLM_BASE_URL')!r}",
    )

    # Explicit disable flag -> nothing configured.
    _reset_env()
    os.environ["KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK"] = "1"
    bootstrap._configure_llm_fallback()
    ok &= _check(
        "disable flag suppresses fallback",
        "KUMIHO_LLM_BASE_URL" not in os.environ and "OPENAI_API_KEY" not in os.environ,
        f"base={os.environ.get('KUMIHO_LLM_BASE_URL')!r} key={os.environ.get('OPENAI_API_KEY')!r}",
    )

    return ok


def main() -> int:
    # Point the credential cache at an empty dir so a real ~/.kumiho token on the
    # test machine cannot leak in and trigger a live discovery call.
    os.environ["KUMIHO_CONFIG_DIR"] = tempfile.mkdtemp(prefix="kumiho-ce-test-")

    tests = (
        ("ce_mode_detection", test_ce_mode_detection),
        ("endpoint_resolution", test_endpoint_resolution),
        ("ce_env_wiring", test_ce_env_wiring),
        ("cloud_default_preserved", test_cloud_default_preserved),
        ("llm_fallback", test_llm_fallback),
    )

    all_ok = True
    for name, fn in tests:
        print(f"\n=== {name} ===")
        all_ok &= fn()

    print("\n" + ("PASS: all CE-mode checks passed" if all_ok else "FAIL: some CE-mode checks failed"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
