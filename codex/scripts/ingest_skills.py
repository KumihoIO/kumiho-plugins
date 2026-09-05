#!/usr/bin/env python3
"""Ingest all bundled Codex skills and references into Codex-owned graph items."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent
# Onboarding launches this file with ``python -I``. Add only the trusted
# sibling directory so the Cloud adapter can be imported without restoring
# CWD, PYTHONPATH, or user-site imports.
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SKILL_MD = PLUGIN_DIR / "skills" / "kumiho-memory" / "SKILL.md"
REFS_DIR = PLUGIN_DIR / "skills" / "kumiho-memory" / "references"
DEFAULT_CE_REDIS_URL = "redis://127.0.0.1:6379"
INGEST_PROJECT = "CognitiveMemory"
INGEST_SPACE = "Skills"
CODEX_ITEM_PREFIX = "codex-kumiho-memory"
CE_ENDPOINT_SCHEMES = {"grpc", "grpcs", "http", "https"}
TRANSPORT_ROUTING_ENV = (
    "KUMIHO_SERVER_USE_TLS",
    "KUMIHO_SERVER_AUTHORITY",
    "KUMIHO_SSL_TARGET_OVERRIDE",
    "KUMIHO_SERVER_CA_FILE",
    "KUMIHO_REQUIRE_TLS",
    "KUMIHO_UPSTASH_REDIS_URL",
    "KUMIHO_MEMORY_PROXY_URL",
    "KUMIHO_MCP_HOSTED",
    "KUMIHO_HOSTED_LOCAL_REDIS",
    "KUMIHO_LOCAL_REDIS_URL",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _config_dir() -> Path:
    override = (os.getenv("KUMIHO_CONFIG_DIR", "") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".kumiho"


def _normalize_endpoint(raw: object) -> str:
    if not isinstance(raw, str):
        raise RuntimeError("CE endpoint must be a string")
    value = raw.strip()
    if not value or any(char in value for char in "\r\n\0"):
        raise RuntimeError("CE endpoint must be a non-empty host:port")
    has_scheme = "://" in value
    parsed = urlsplit(value if has_scheme else f"//{value}")
    scheme = parsed.scheme.lower() if has_scheme else ""
    if scheme and scheme not in CE_ENDPOINT_SCHEMES:
        raise RuntimeError("CE endpoint has an unsupported scheme")
    if (
        parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("CE endpoint contains unsupported URL components")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("CE endpoint has an invalid port") from exc
    if not parsed.hostname:
        raise RuntimeError("CE endpoint must include a host")
    if port is None:
        if not scheme:
            raise RuntimeError("CE endpoint must include a port")
        port = 443 if scheme in {"https", "grpcs"} else 80
    host = parsed.hostname
    loopback_host = host.rstrip(".").lower()
    loopback = loopback_host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(loopback_host).is_loopback
        except ValueError:
            loopback = False
    if not loopback:
        raise RuntimeError("CE endpoints must use a loopback host")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}"
    return f"{scheme}://{authority}" if scheme else authority


def _validate_url(
    raw: object,
    *,
    schemes: set[str],
    label: str,
    require_tls_for_remote: bool = False,
) -> str:
    if not isinstance(raw, str):
        raise RuntimeError(f"{label} must be a string")
    value = raw.strip()
    if not value or any(char in value for char in "\r\n\0"):
        raise RuntimeError(f"{label} is empty or invalid")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in schemes or not parsed.netloc:
        raise RuntimeError(f"{label} has an unsupported URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError(f"{label} must not contain credentials, query, or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{label} has an invalid port") from exc
    if require_tls_for_remote:
        host = (parsed.hostname or "").rstrip(".").lower()
        loopback = host == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
        if not loopback:
            raise RuntimeError(f"{label} must use a loopback host")
    return value


def _load_config(path: Path, expected_backend: str) -> dict:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read Codex backend config: {exc}") from exc
    if not isinstance(body, dict) or body.get("schema_version") != 1:
        raise RuntimeError("unsupported Codex backend config")
    if body.get("backend") != expected_backend:
        raise RuntimeError("Codex backend config changed during onboarding")
    if expected_backend == "cloud":
        return {"schema_version": 1, "backend": "cloud"}

    validated = {
        "schema_version": 1,
        "backend": "ce",
        "endpoint": _normalize_endpoint(body.get("endpoint")),
        "redis_url": _validate_url(
            body.get("redis_url") or DEFAULT_CE_REDIS_URL,
            schemes={"redis", "rediss"},
            label="CE Redis URL",
            require_tls_for_remote=True,
        ),
    }
    if body.get("llm_base_url"):
        validated["llm_base_url"] = _validate_url(
            body["llm_base_url"],
            schemes={"http", "https"},
            label="CE LLM URL",
            require_tls_for_remote=True,
        )
    return validated


def _configure_backend(backend: str, config: dict) -> None:
    # ``kumiho`` may auto-configure during import. Neutralize that switch and
    # every inherited discovery route before importing the SDK; backend
    # binding below is explicit for both Cloud and CE.
    os.environ.pop("KUMIHO_AUTO_CONFIGURE", None)
    os.environ.pop("KUMIHO_DISCOVERY_CACHE_FILE", None)
    for key in TRANSPORT_ROUTING_ENV:
        os.environ.pop(key, None)
    if backend == "cloud":
        # Use the exact same adapter as MCP startup. It pins official Cloud
        # routing while the Python SDK owns token handling and discovery.
        import run_kumiho_cloud as cloud_adapter

        try:
            cloud_adapter._prepare_environment()
            authenticated = cloud_adapter._configure_cloud(force_refresh=True)
        except ImportError:
            raise
        except Exception as exc:
            raise RuntimeError(
                "Codex Cloud adapter could not configure official discovery"
            ) from exc
        if not authenticated:
            raise RuntimeError(
                "Cloud authentication or official discovery is unavailable; "
                "run $kumiho-onboard"
            )
        return

    for key in (
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_TENANT_HINT",
        "KUMIHO_FIREBASE_API_KEY",
        "KUMIHO_FIREBASE_ID_TOKEN",
        "KUMIHO_FIREBASE_PROJECT_ID",
        "KUMIHO_USE_CONTROL_PLANE_TOKEN",
        "KUMIHO_WORKSPACE_ROOT",
        "KUMIHO_ENV_FILE",
        "KUMIHO_CLAUDE_MODE",
        "KUMIHO_CLAUDE_SERVER_ENDPOINT",
        "KUMIHO_LOCAL_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ADDRESS",
        "UPSTASH_REDIS_URL",
        "KUMIHO_LLM_BASE_URL",
    ):
        os.environ.pop(key, None)
    os.environ["KUMIHO_AUTH_TOKEN"] = ""

    try:
        import kumiho
    except ImportError as exc:
        raise RuntimeError("kumiho client is unavailable") from exc

    endpoint = _normalize_endpoint(config.get("endpoint"))
    redis_url = _validate_url(
        config.get("redis_url") or DEFAULT_CE_REDIS_URL,
        schemes={"redis", "rediss"},
        label="CE Redis URL",
        require_tls_for_remote=True,
    )
    os.environ["KUMIHO_AUTH_TOKEN"] = ""
    os.environ.pop("KUMIHO_CONTROL_PLANE_URL", None)
    os.environ.pop("KUMIHO_CONTROL_PLANE_API_URL", None)
    os.environ.pop("KUMIHO_TENANT_HINT", None)
    os.environ["KUMIHO_CLAUDE_MODE"] = "ce"
    os.environ["KUMIHO_CLAUDE_SERVER_ENDPOINT"] = endpoint
    os.environ.pop("KUMIHO_LOCAL_SERVER_ENDPOINT", None)
    os.environ["KUMIHO_SERVER_ENDPOINT"] = endpoint
    scheme = endpoint.partition("://")[0].lower() if "://" in endpoint else ""
    if scheme in {"grpcs", "https"}:
        os.environ["KUMIHO_SERVER_USE_TLS"] = "true"
        os.environ["KUMIHO_REQUIRE_TLS"] = "1"
    else:
        os.environ["KUMIHO_SERVER_USE_TLS"] = "false"
    os.environ["UPSTASH_REDIS_URL"] = redis_url
    os.environ.pop("KUMIHO_SERVER_ADDRESS", None)
    llm_base_url = config.get("llm_base_url")
    os.environ.pop("KUMIHO_LLM_BASE_URL", None)
    if llm_base_url:
        os.environ["KUMIHO_LLM_BASE_URL"] = _validate_url(
            llm_base_url,
            schemes={"http", "https"},
            label="CE LLM URL",
            require_tls_for_remote=True,
        )
    client = kumiho.connect(
        endpoint=endpoint,
        # Empty is intentionally different from None in the SDK: None reloads
        # the shared Cloud credential cache and could send that bearer to CE.
        token="",
        enable_auto_login=False,
        use_discovery=False,
    )
    kumiho.configure_default_client(client)


def _ensure_ingest_project(kumiho) -> None:
    """Create the shared memory project on a brand-new backend, race-safely."""
    if kumiho.get_project(INGEST_PROJECT) is not None:
        return
    try:
        kumiho.create_project(
            INGEST_PROJECT,
            "Persistent cognitive memory shared by Kumiho agents",
        )
    except Exception:
        # Another onboarding process may have created it concurrently.
        if kumiho.get_project(INGEST_PROJECT) is None:
            raise


def _enable_codex_agent_compat(skill_ingest) -> None:
    # These documents contain Codex-specific command and session semantics.
    # Never move the shared ``published`` tag under Claude's canonical items.
    skill_ingest.DEFAULT_AGENT_COMPAT = ["codex"]


def _ingest_documents(skill_ingest, *, dry_run: bool) -> list:
    documents = []
    for skill_md in sorted((PLUGIN_DIR / "skills").glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        # Preserve existing memory/ref item names and Claude's published tags.
        prefix = CODEX_ITEM_PREFIX if skill_name == "kumiho-memory" else f"codex-{skill_name}"
        documents.append((skill_md, prefix))
        documents.extend(
            (path, f"{prefix}-ref-{path.stem}")
            for path in sorted((skill_md.parent / "references").glob("*.md"))
        )
    return [
        skill_ingest.ingest_file(
            path,
            item_name=item_name,
            project=INGEST_PROJECT,
            space_name=INGEST_SPACE,
            tags=["codex", "kumiho-memory"],
            dry_run=dry_run,
        )
        for path, item_name in documents
    ]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest Codex Kumiho skill references into the memory graph."
    )
    parser.add_argument("--backend", required=True, choices=("cloud", "ce"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    args = _parse_args(argv)
    if not SKILL_MD.is_file():
        print(f"[kumiho-codex] Memory skill not found: {SKILL_MD}", file=sys.stderr)
        return 1

    try:
        config = _load_config(args.config, args.backend)
        if not args.dry_run:
            _configure_backend(args.backend, config)
        import kumiho
        from kumiho_memory import skill_ingest
    except (ImportError, RuntimeError) as exc:
        print(f"[kumiho-codex] Cannot prepare skill ingestion: {exc}", file=sys.stderr)
        return 1

    try:
        _enable_codex_agent_compat(skill_ingest)
        if not args.dry_run:
            _ensure_ingest_project(kumiho)
        documents = _ingest_documents(skill_ingest, dry_run=args.dry_run)
    except Exception as exc:
        # Do not echo environment values or serialized request bodies here: an
        # SDK exception can contain headers on some transports.
        print(
            f"[kumiho-codex] Skill ingestion failed ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1

    action = "Would ingest" if args.dry_run else "Ingested"
    print(
        f"[kumiho-codex] {action} {len(documents)} "
        "skill documents into CognitiveMemory/Skills."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
