#!/usr/bin/env python3
"""Offline unit checks for self-hosted CE (Community Edition) mode.

Exercises the CE-mode branch of the launcher without any network or venv:
detection, endpoint resolution, SDK env wiring, LLM-endpoint handling, and the
official Cloud adapter environment boundary.

Usage (from kumiho-claude/ or repo root):
    python kumiho-claude/scripts/test_ce_mode.py
"""

from __future__ import annotations

import os
import sys
import tempfile

import run_kumiho_cloud as cloud
import run_kumiho_mcp as bootstrap


# Every env var the CE / cloud / LLM paths read or write, cleared between cases.
_MANAGED_KEYS = (
    "KUMIHO_CLAUDE_MODE",
    "KUMIHO_CLAUDE_SERVER_ENDPOINT",
    "KUMIHO_LOCAL_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ENDPOINT",
    "KUMIHO_SERVER_ADDRESS",
    "KUMIHO_SERVER_USE_TLS",
    "KUMIHO_SERVER_AUTHORITY",
    "KUMIHO_SSL_TARGET_OVERRIDE",
    "KUMIHO_SERVER_CA_FILE",
    "KUMIHO_REQUIRE_TLS",
    "KUMIHO_AUTH_TOKEN",
    "KUMIHO_TENANT_HINT",
    "KUMIHO_CONTROL_PLANE_URL",
    "KUMIHO_CONTROL_PLANE_API_URL",
    "KUMIHO_DISCOVERY_CACHE_FILE",
    "KUMIHO_PLUGIN_SHARED_HOME",
    "UPSTASH_REDIS_URL",
    "KUMIHO_WORKING_MEMORY_TTL",
    "KUMIHO_LLM_BASE_URL",
    "KUMIHO_LLM_PROVIDER",
    "KUMIHO_LLM_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "AZURE_OPENAI_ENDPOINT",
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

    assert ok


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
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "grpc://127.0.0.1:9200"
    resolved = bootstrap._resolve_ce_endpoint()
    ok &= _check(
        "loopback plaintext scheme preserved",
        resolved == "grpc://127.0.0.1:9200",
        f"got {resolved!r}",
    )

    _reset_env()
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "grpcs://myhost:7443"
    try:
        bootstrap._resolve_ce_endpoint()
    except SystemExit:
        pass
    else:
        ok &= _check("remote TLS CE endpoint is rejected", False)

    _reset_env()
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "grpc://myhost:9200"
    try:
        bootstrap._resolve_ce_endpoint()
    except SystemExit:
        pass
    else:
        ok &= _check("remote plaintext CE endpoint is rejected", False)

    _reset_env()
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "127.0.0.1:9190"
    resolved = bootstrap._resolve_ce_endpoint()
    ok &= _check("host:port passthrough", resolved == "127.0.0.1:9190", f"got {resolved!r}")

    assert ok


def test_ce_env_wiring() -> bool:
    ok = True

    # A stray cloud token + endpoint must be neutralized in CE mode.
    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["KUMIHO_AUTH_TOKEN"] = "eyJstray.cloud.token"
    os.environ["KUMIHO_SERVER_ENDPOINT"] = "us-central.kumiho.cloud:443"
    os.environ["KUMIHO_SERVER_USE_TLS"] = "true"
    os.environ["KUMIHO_SERVER_AUTHORITY"] = "stale-authority"
    os.environ["KUMIHO_SSL_TARGET_OVERRIDE"] = "stale-target"
    os.environ["KUMIHO_SERVER_CA_FILE"] = "stale-ca.pem"
    os.environ["KUMIHO_REQUIRE_TLS"] = "1"
    bootstrap._bootstrap_ce_endpoint(bootstrap._resolve_ce_endpoint())
    ok &= _check(
        "explicit endpoint set to CE default",
        os.environ.get("KUMIHO_SERVER_ENDPOINT") == bootstrap.DEFAULT_CE_ENDPOINT,
        f"got {os.environ.get('KUMIHO_SERVER_ENDPOINT')!r}",
    )
    ok &= _check(
        "loopback-only discovery endpoint cleared",
        "KUMIHO_LOCAL_SERVER_ENDPOINT" not in os.environ,
        f"got {os.environ.get('KUMIHO_LOCAL_SERVER_ENDPOINT')!r}",
    )
    ok &= _check(
        "token blanked to force tokenless CE",
        os.environ.get("KUMIHO_AUTH_TOKEN") == "",
        f"got {os.environ.get('KUMIHO_AUTH_TOKEN')!r}",
    )
    ok &= _check(
        "bare loopback endpoint deterministically disables TLS",
        os.environ.get("KUMIHO_SERVER_USE_TLS") == "false"
        and all(
            key not in os.environ
            for key in (
                "KUMIHO_SERVER_AUTHORITY",
                "KUMIHO_SSL_TARGET_OVERRIDE",
                "KUMIHO_SERVER_CA_FILE",
                "KUMIHO_REQUIRE_TLS",
            )
        ),
        "stale transport override survived",
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

    # A user-provided loopback Redis URL must win over the default.
    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["UPSTASH_REDIS_URL"] = "rediss://127.0.0.1:6380"
    os.environ["KUMIHO_WORKING_MEMORY_TTL"] = "7200"
    bootstrap._bootstrap_ce_endpoint(bootstrap._resolve_ce_endpoint())
    ok &= _check(
        "user Redis URL preserved",
        os.environ.get("UPSTASH_REDIS_URL") == "rediss://127.0.0.1:6380",
        f"got {os.environ.get('UPSTASH_REDIS_URL')!r}",
    )
    ok &= _check(
        "user working-memory TTL preserved",
        os.environ.get("KUMIHO_WORKING_MEMORY_TTL") == "7200",
        f"got {os.environ.get('KUMIHO_WORKING_MEMORY_TTL')!r}",
    )

    _reset_env()
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = "grpcs://127.0.0.1:7443"
    os.environ["KUMIHO_SERVER_USE_TLS"] = "false"
    bootstrap._bootstrap_ce_endpoint(bootstrap._resolve_ce_endpoint())
    ok &= _check(
        "loopback TLS CE deterministically requires TLS",
        os.environ.get("KUMIHO_SERVER_USE_TLS") == "true"
        and os.environ.get("KUMIHO_REQUIRE_TLS") == "1",
        "secure CE transport was not pinned",
    )

    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["UPSTASH_REDIS_URL"] = "rediss://redis.example.test:6380/0"
    try:
        bootstrap._bootstrap_ce_endpoint(bootstrap._resolve_ce_endpoint())
    except SystemExit:
        pass
    else:
        ok &= _check("remote TLS CE Redis is rejected", False)

    assert ok


def test_cloud_environment_boundary() -> bool:
    ok = True
    _reset_env()
    shared_root = os.environ["KUMIHO_CONFIG_DIR"]
    os.environ[cloud.SHARED_HOME_HANDOFF_ENV] = shared_root
    os.environ["KUMIHO_AUTH_TOKEN"] = "explicit-api-token"
    os.environ["KUMIHO_CONTROL_PLANE_URL"] = "https://private.example.test"
    os.environ["KUMIHO_CONTROL_PLANE_API_URL"] = "https://auth.example.test"
    os.environ["KUMIHO_DISCOVERY_CACHE_FILE"] = os.path.join(
        shared_root, "legacy-discovery-cache.json"
    )
    os.environ["KUMIHO_SERVER_ENDPOINT"] = "127.0.0.1:9190"
    resolved_root = cloud._prepare_environment()
    ok &= _check(
        "Cloud discovery is pinned to the official control plane",
        os.environ.get("KUMIHO_CONTROL_PLANE_URL")
        == cloud.OFFICIAL_CONTROL_PLANE_URL,
        f"got {os.environ.get('KUMIHO_CONTROL_PLANE_URL')!r}",
    )
    ok &= _check(
        "Cloud auth API override remains unset",
        "KUMIHO_CONTROL_PLANE_API_URL" not in os.environ,
    )
    ok &= _check(
        "explicit API token is passed through to the SDK",
        os.environ.get("KUMIHO_AUTH_TOKEN") == "explicit-api-token",
    )
    expected_cache = os.path.join(
        shared_root,
        cloud.OFFICIAL_DISCOVERY_CACHE_DIRNAME,
        "discovery-cache.json",
    )
    ok &= _check(
        "official discovery uses an origin-scoped cache",
        str(resolved_root) == shared_root
        and os.environ.get("KUMIHO_DISCOVERY_CACHE_FILE") == expected_cache,
        f"got {os.environ.get('KUMIHO_DISCOVERY_CACHE_FILE')!r}",
    )
    ok &= _check(
        "Cloud adapter removes CE endpoint overrides",
        "KUMIHO_SERVER_ENDPOINT" not in os.environ,
    )
    assert ok


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

    # A real ambient key must never be sent to a plaintext remote model.
    _reset_env()
    os.environ["KUMIHO_LLM_BASE_URL"] = "http://llm.example.test/v1"
    os.environ["OPENAI_API_KEY"] = "sk-must-not-leave"
    bootstrap._configure_llm_fallback()
    ok &= _check(
        "remote plaintext LLM is pinned to the keyless dead port",
        os.environ.get("KUMIHO_LLM_BASE_URL") == "http://127.0.0.1:9/v1"
        and os.environ.get("OPENAI_API_KEY") == "kumiho-claude-fallback",
        "unsafe remote LLM configuration survived",
    )

    # CE never sends enrichment payloads or provider keys off-machine, even
    # when a stale user-global endpoint uses HTTPS.
    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["KUMIHO_LLM_BASE_URL"] = "https://llm.example.test/v1"
    os.environ["OPENAI_API_KEY"] = "sk-must-not-leave"
    bootstrap._configure_llm_fallback()
    ok &= _check(
        "remote HTTPS CE LLM is pinned to the keyless dead port",
        os.environ.get("KUMIHO_LLM_BASE_URL") == "http://127.0.0.1:9/v1"
        and os.environ.get("OPENAI_API_KEY") == "kumiho-claude-fallback",
        "remote CE LLM configuration survived",
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

    # Disabling the convenience fallback must not disable the CE trust
    # boundary itself: a remote model is forbidden even when it uses TLS.
    _reset_env()
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK"] = "1"
    os.environ["KUMIHO_LLM_BASE_URL"] = "https://llm.example.test/v1"
    os.environ["OPENAI_API_KEY"] = "sk-must-not-leave"
    bootstrap._configure_llm_fallback()
    ok &= _check(
        "disable flag cannot bypass the CE loopback-only LLM boundary",
        os.environ.get("KUMIHO_LLM_BASE_URL") != "https://llm.example.test/v1"
        and os.environ.get("OPENAI_API_KEY") != "sk-must-not-leave",
        "unsafe remote CE LLM configuration survived the disable gate",
    )

    for key_name in (
        "KUMIHO_LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        for disable in (False, True):
            _reset_env()
            os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
            if disable:
                os.environ["KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK"] = "1"
            os.environ[key_name] = "provider-key-must-not-leave"
            bootstrap._configure_llm_fallback()
            ok &= _check(
                f"CE rejects key-only {key_name} config (disable={disable})",
                os.environ.get(key_name) != "provider-key-must-not-leave",
                f"{key_name} could select its default remote provider",
            )

    for alias in (
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "AZURE_OPENAI_ENDPOINT",
    ):
        _reset_env()
        os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
        os.environ[alias] = "https://llm.example.test/v1"
        os.environ["OPENAI_API_KEY"] = "sk-must-not-leave"
        bootstrap._configure_llm_fallback()
        ok &= _check(
            f"CE rejects remote provider alias {alias}",
            alias not in os.environ
            and os.environ.get("OPENAI_API_KEY") != "sk-must-not-leave",
            f"unsafe {alias} survived CE validation",
        )

    assert ok


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
    custom_pairs = dict(setup._ce_persist_pairs({"endpoint": "127.0.0.1:9", "redis_url": "redis://127.0.0.1:6380", "llm_base_url": "http://127.0.0.1:11434/v1"}))
    ok &= _check(
        "custom persist includes overrides",
        custom_pairs == {"KUMIHO_CLAUDE_MODE": "ce", "KUMIHO_CLAUDE_SERVER_ENDPOINT": "127.0.0.1:9",
                         "UPSTASH_REDIS_URL": "redis://127.0.0.1:6380", "KUMIHO_LLM_BASE_URL": "http://127.0.0.1:11434/v1"},
        f"got {custom_pairs}",
    )

    ok &= _check(
        "loopback HTTP LLM is allowed",
        setup._validate_ce_url(
            "http://127.0.0.1:11434/v1",
            schemes={"http", "https"},
            label="CE LLM URL",
            require_tls_for_remote=True,
        ) == "http://127.0.0.1:11434/v1",
    )
    try:
        setup._validate_ce_url(
            "http://llm.example.test/v1",
            schemes={"http", "https"},
            label="CE LLM URL",
            require_tls_for_remote=True,
        )
    except ValueError:
        pass
    else:
        ok &= _check("remote CE LLM is rejected", False)

    try:
        setup._validate_ce_url(
            "https://llm.example.test/v1",
            schemes={"http", "https"},
            label="CE LLM URL",
            require_tls_for_remote=True,
        )
    except ValueError:
        pass
    else:
        ok &= _check("remote HTTPS CE LLM is rejected", False)

    try:
        setup._validate_ce_url(
            "rediss://redis.example.test:6380/0",
            schemes={"redis", "rediss"},
            label="CE Redis URL",
            require_tls_for_remote=True,
        )
    except ValueError:
        pass
    else:
        ok &= _check("remote TLS CE Redis is rejected by onboarding", False)

    # Runtime env for ingest/verify is tokenless and points at the CE endpoint.
    rt = setup._ce_runtime_env({"endpoint": "127.0.0.1:9", "redis_url": "", "llm_base_url": ""})
    ok &= _check(
        "runtime env is tokenless CE",
        rt == {
            "KUMIHO_CLAUDE_MODE": "ce",
            "KUMIHO_SERVER_ENDPOINT": "127.0.0.1:9",
            "KUMIHO_AUTH_TOKEN": "",
            "UPSTASH_REDIS_URL": setup.DEFAULT_CE_REDIS_URL,
        },
        f"got {rt}",
    )

    # The wizard must carry that endpoint through to the ingestion child. A
    # previous cleanup step accidentally popped the freshly written CE value,
    # making every Claude CE onboarding fail at stage 4.
    captured = {}
    original_run = setup.subprocess.run

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return types.SimpleNamespace(returncode=0)

    os.environ["KUMIHO_SERVER_ENDPOINT"] = "cloud.example.test:443"
    os.environ["KUMIHO_SERVER_ADDRESS"] = "cloud-alias.example.test:443"
    setup.subprocess.run = fake_run
    try:
        setup.run_ingestion(
            setup.VENV_PYTHON,
            ce_env={"endpoint": "grpcs://127.0.0.1:7443", "redis_url": ""},
        )
    finally:
        setup.subprocess.run = original_run
    ok &= _check(
        "CE ingestion keeps explicit endpoint",
        captured.get("env", {}).get("KUMIHO_SERVER_ENDPOINT")
        == "grpcs://127.0.0.1:7443"
        and "KUMIHO_SERVER_ADDRESS" not in captured.get("env", {})
        and captured.get("command") == [
            str(setup.VENV_PYTHON),
            "-I",
            str(setup.CE_RUNNER),
            "--script",
            str(setup.INGEST_SCRIPT),
        ],
        "endpoint=%r legacy_address_present=%r command=%r"
        % (
            captured.get("env", {}).get("KUMIHO_SERVER_ENDPOINT"),
            "KUMIHO_SERVER_ADDRESS" in captured.get("env", {}),
            captured.get("command"),
        ),
    )

    # A down endpoint probes False (no crash, no false positive).
    ok &= _check("probe of down endpoint is False", setup._probe_ce("127.0.0.1:9199", timeout=1.0) is False)

    # Endpoint normalization strips scheme/path so the probe URL stays valid.
    ok &= _check(
        "normalize scheme URL without stripping it",
        setup._normalize_endpoint("grpc://127.0.0.1:9190")
        == "grpc://127.0.0.1:9190",
    )
    ok &= _check("normalize trailing path", setup._normalize_endpoint("127.0.0.1:9190/") == "127.0.0.1:9190")
    ok &= _check("normalize plain passthrough", setup._normalize_endpoint("127.0.0.1:9190") == "127.0.0.1:9190")
    ce_norm = setup.setup_ce(ns(ce=True, ce_endpoint="grpcs://127.0.0.1:9191"))
    ok &= _check(
        "setup_ce preserves endpoint scheme",
        ce_norm["endpoint"] == "grpcs://127.0.0.1:9191",
        f"got {ce_norm['endpoint']}",
    )

    # Backend-switch cleanup: cloud re-onboarding must strip stale CE markers
    # from the Desktop config so the launcher does not trap the user in CE mode.
    import json as _json
    tmp_cfg = os.path.join(tempfile.mkdtemp(prefix="kumiho-cfg-"), "claude_desktop_config.json")
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        _json.dump({"mcpServers": {"kumiho-memory": {"env": {
            "KUMIHO_AUTH_TOKEN": "eyJnew", "KUMIHO_CLAUDE_MODE": "ce",
            "KUMIHO_CLAUDE_SERVER_ENDPOINT": "127.0.0.1:9190",
            "UPSTASH_REDIS_URL": "redis://127.0.0.1:6379",
            "KUMIHO_LLM_BASE_URL": "http://127.0.0.1:11434/v1"}}}}, f)
    removed = setup._delete_env_from_config(
        __import__("pathlib").Path(tmp_cfg), list(setup._CE_PERSISTED_ENV_KEYS))
    with open(tmp_cfg, encoding="utf-8") as f:
        after = _json.load(f)["mcpServers"]["kumiho-memory"]["env"]
    ok &= _check("delete removed CE markers", removed is True)
    ok &= _check("token preserved after delete", after.get("KUMIHO_AUTH_TOKEN") == "eyJnew")
    ok &= _check(
        "all CE routes gone after delete",
        all(key not in after for key in setup._CE_PERSISTED_ENV_KEYS),
        f"remaining env: {after}",
    )
    ok &= _check(
        "cloud cleanup owns every CE-persisted key",
        setup._CE_PERSISTED_ENV_KEYS == (
            "KUMIHO_CLAUDE_MODE", "KUMIHO_CLAUDE_SERVER_ENDPOINT",
            "UPSTASH_REDIS_URL", "KUMIHO_LLM_BASE_URL",
        ),
    )
    ok &= _check("delete no-op returns False", setup._delete_env_from_config(__import__("pathlib").Path(tmp_cfg), list(setup._CE_PERSISTED_ENV_KEYS)) is False)

    assert ok


def main() -> int:
    # Point the credential cache at an empty dir so a real ~/.kumiho token on the
    # test machine cannot leak in and trigger a live discovery call.
    os.environ["KUMIHO_CONFIG_DIR"] = tempfile.mkdtemp(prefix="kumiho-ce-test-")

    tests = (
        ("ce_mode_detection", test_ce_mode_detection),
        ("endpoint_resolution", test_endpoint_resolution),
        ("ce_env_wiring", test_ce_env_wiring),
        ("cloud_environment_boundary", test_cloud_environment_boundary),
        ("llm_fallback", test_llm_fallback),
        ("setup_wizard_ce", test_setup_wizard_ce),
    )

    all_ok = True
    for name, fn in tests:
        print(f"\n=== {name} ===")
        try:
            fn()
        except AssertionError:
            all_ok = False

    print("\n" + ("PASS: all CE-mode checks passed" if all_ok else "FAIL: some CE-mode checks failed"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
