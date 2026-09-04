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
    "KUMIHO_WORKING_MEMORY_TTL",
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
    ok &= _check(
        "default working-memory TTL supplied",
        os.environ.get("KUMIHO_WORKING_MEMORY_TTL") == bootstrap.DEFAULT_CE_WORKING_MEMORY_TTL,
        f"got {os.environ.get('KUMIHO_WORKING_MEMORY_TTL')!r}",
    )

    # A user-provided Redis URL must win over the default.
    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["UPSTASH_REDIS_URL"] = "redis://10.0.0.5:6380"
    os.environ["KUMIHO_WORKING_MEMORY_TTL"] = "7200"
    bootstrap._bootstrap_server_endpoint()
    ok &= _check(
        "user Redis URL preserved",
        os.environ.get("UPSTASH_REDIS_URL") == "redis://10.0.0.5:6380",
        f"got {os.environ.get('UPSTASH_REDIS_URL')!r}",
    )
    ok &= _check(
        "user working-memory TTL preserved",
        os.environ.get("KUMIHO_WORKING_MEMORY_TTL") == "7200",
        f"got {os.environ.get('KUMIHO_WORKING_MEMORY_TTL')!r}",
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


def test_setup_wizard_ce() -> bool:
    """The onboarding wizard's CE helpers: backend choice, persisted pairs, and
    the tokenless runtime env handed to ingestion/verify."""
    import types

    import setup

    setup.AUTO_YES = True

    def ns(**kw):
        base = {
            "ce": False, "ce_endpoint": None, "ce_redis_url": None,
            "ce_llm_base_url": None, "token": None, "yes": True,
        }
        base.update(kw)
        return types.SimpleNamespace(**base)

    ok = True

    # Backend selection preserves the cloud default; CE is opt-in.
    ok &= _check("--ce selects CE", setup.choose_backend(ns(ce=True)) == "ce")
    ok &= _check("token selects cloud", setup.choose_backend(ns(token="eyJ")) == "cloud")
    ok &= _check("non-interactive default is cloud", setup.choose_backend(ns()) == "cloud")

    # Persisted pairs are minimal at defaults, complete when overridden.
    default_pairs = setup._ce_persist_pairs({"endpoint": setup.DEFAULT_CE_ENDPOINT, "redis_url": "", "llm_base_url": ""})
    ok &= _check("default persist = mode only", default_pairs == [("KUMIHO_CLAUDE_MODE", "ce")], f"got {default_pairs}")
    custom_pairs = dict(setup._ce_persist_pairs({"endpoint": "h:9", "redis_url": "redis://r:1", "llm_base_url": "http://l/v1"}))
    ok &= _check(
        "custom persist includes overrides",
        custom_pairs == {"KUMIHO_CLAUDE_MODE": "ce", "KUMIHO_CLAUDE_SERVER_ENDPOINT": "h:9",
                         "UPSTASH_REDIS_URL": "redis://r:1", "KUMIHO_LLM_BASE_URL": "http://l/v1"},
        f"got {custom_pairs}",
    )

    # Runtime env for ingest/verify is tokenless and points at the CE endpoint.
    rt = setup._ce_runtime_env({"endpoint": "h:9", "redis_url": "", "llm_base_url": ""})
    ok &= _check(
        "runtime env is tokenless CE",
        rt == {"KUMIHO_LOCAL_SERVER_ENDPOINT": "h:9", "KUMIHO_AUTH_TOKEN": "", "UPSTASH_REDIS_URL": setup.DEFAULT_CE_REDIS_URL},
        f"got {rt}",
    )

    # A down endpoint probes False (no crash, no false positive).
    ok &= _check("probe of down endpoint is False", setup._probe_ce("127.0.0.1:9199", timeout=1.0) is False)

    # Endpoint normalization strips scheme/path so the probe URL stays valid.
    ok &= _check("normalize scheme URL", setup._normalize_endpoint("grpc://127.0.0.1:9190") == "127.0.0.1:9190")
    ok &= _check("normalize trailing path", setup._normalize_endpoint("127.0.0.1:9190/") == "127.0.0.1:9190")
    ok &= _check("normalize plain passthrough", setup._normalize_endpoint("127.0.0.1:9190") == "127.0.0.1:9190")
    ce_norm = setup.setup_ce(ns(ce=True, ce_endpoint="grpc://5.6.7.8:9191"))
    ok &= _check("setup_ce normalizes endpoint", ce_norm["endpoint"] == "5.6.7.8:9191", f"got {ce_norm['endpoint']}")

    # Backend-switch cleanup: cloud re-onboarding must strip stale CE markers
    # from the Desktop config so the launcher does not trap the user in CE mode.
    import json as _json
    tmp_cfg = os.path.join(tempfile.mkdtemp(prefix="kumiho-cfg-"), "claude_desktop_config.json")
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        _json.dump({"mcpServers": {"kumiho-memory": {"env": {
            "KUMIHO_AUTH_TOKEN": "eyJnew", "KUMIHO_CLAUDE_MODE": "ce",
            "KUMIHO_CLAUDE_SERVER_ENDPOINT": "127.0.0.1:9190"}}}}, f)
    removed = setup._delete_env_from_config(
        __import__("pathlib").Path(tmp_cfg), ["KUMIHO_CLAUDE_MODE", "KUMIHO_CLAUDE_SERVER_ENDPOINT"])
    with open(tmp_cfg, encoding="utf-8") as f:
        after = _json.load(f)["mcpServers"]["kumiho-memory"]["env"]
    ok &= _check("delete removed CE markers", removed is True)
    ok &= _check("token preserved after delete", after.get("KUMIHO_AUTH_TOKEN") == "eyJnew")
    ok &= _check("CE markers gone after delete", "KUMIHO_CLAUDE_MODE" not in after and "KUMIHO_CLAUDE_SERVER_ENDPOINT" not in after)
    ok &= _check("delete no-op returns False", setup._delete_env_from_config(__import__("pathlib").Path(tmp_cfg), ["KUMIHO_CLAUDE_MODE"]) is False)

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
        ("setup_wizard_ce", test_setup_wizard_ce),
    )

    all_ok = True
    for name, fn in tests:
        print(f"\n=== {name} ===")
        all_ok &= fn()

    print("\n" + ("PASS: all CE-mode checks passed" if all_ok else "FAIL: some CE-mode checks failed"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
