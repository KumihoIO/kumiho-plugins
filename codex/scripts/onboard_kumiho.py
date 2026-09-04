#!/usr/bin/env python3
"""Secure, host-isolated onboarding for the native Codex plugin.

The wizard intentionally accepts no token or password argument.  Cloud
credentials are acquired only by ``kumiho.auth_cli`` on an interactive TTY and
stored below ``~/.kumiho/codex-cloud``. The backend file written here contains only
non-secret Codex backend selection, so it cannot alter Claude's plugin config.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent
PYTHON_LAUNCHER = SCRIPT_DIR / "run_kumiho_mcp.py"
INGEST_SCRIPT = SCRIPT_DIR / "ingest_skills.py"
VERIFY_SCRIPT = SCRIPT_DIR / "verify_backend.py"
CLOUD_RUNNER = SCRIPT_DIR / "run_kumiho_cloud.py"

CONFIG_SCHEMA = 1
DEFAULT_CE_ENDPOINT = "127.0.0.1:9190"
DEFAULT_CE_REDIS_URL = "redis://127.0.0.1:6379"
PROVISION_TIMEOUT_S = 15 * 60
AUTH_TIMEOUT_S = 45
OFFICIAL_CONTROL_PLANE_URL = "https://control.kumiho.cloud"
CODEX_AUTH_DIRNAME = "codex-cloud"
INGEST_TIMEOUT_S = 2 * 60

# Installed plugin snapshots execute this file directly, while unit tests may
# load it by path. Resolve the vendored bounded runner from this script's own
# directory in both cases.
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import bounded_proc  # noqa: E402


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


def _config_path() -> Path:
    return _config_dir() / "codex.json"


def _state_dir() -> Path:
    override = (os.getenv("KUMIHO_CLAUDE_HOME", "") or "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "kumiho-claude"
    xdg = (os.getenv("XDG_CACHE_HOME", "") or "").strip()
    return (Path(xdg) if xdg else Path.home() / ".cache") / "kumiho-claude"


def _plugin_data_dir() -> Path | None:
    """Derive Codex-owned plugin data without ever accepting Claude's path."""
    # Prefer the Codex installation path. CLAUDE_PLUGIN_DATA is owned by a
    # different host and can leak into Codex when it is launched from a
    # Claude-managed shell.
    parts = SCRIPT_DIR.parts
    lowered = [part.lower() for part in parts]
    if "cache" in lowered:
        index = len(lowered) - 1 - lowered[::-1].index("cache")
        if len(parts) >= index + 4:
            marketplace, plugin = parts[index + 1], parts[index + 2]
            return Path(*parts[:index]) / "data" / f"{plugin}-{marketplace}"
    return None


def _venv_python() -> Path:
    # Claude and Codex deliberately share one package runtime. Backend choice
    # remains host-specific; only the immutable Python dependencies are shared.
    venv = _config_dir() / "venv"
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _auth_executable(venv_python: Path) -> Path:
    name = "kumiho-auth.exe" if os.name == "nt" else "kumiho-auth"
    return venv_python.parent / name


def _normalize_endpoint(raw: str) -> str:
    value = raw.strip()
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError("CE endpoint must be a non-empty host:port")
    has_scheme = "://" in value
    parsed = urlsplit(value if has_scheme else f"//{value}")
    scheme = parsed.scheme.lower() if has_scheme else ""
    if scheme and scheme not in {"http", "https", "grpc", "grpcs"}:
        raise ValueError("CE endpoint scheme must be http(s) or grpc(s)")
    if (
        parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "CE endpoint must not contain credentials, a path, query, or fragment"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CE endpoint has an invalid port") from exc
    if not parsed.hostname:
        raise ValueError("CE endpoint must include a host")
    if port is None:
        if not scheme:
            raise ValueError("CE endpoint must include a port")
        port = 443 if scheme in {"https", "grpcs"} else 80
    host = parsed.hostname
    plaintext = scheme in {"", "http", "grpc"}
    loopback_host = host.rstrip(".").lower()
    loopback = loopback_host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(loopback_host).is_loopback
        except ValueError:
            loopback = False
    if plaintext and not loopback:
        raise ValueError("remote CE endpoints must use https:// or grpcs://")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{host}:{port}"
    return f"{scheme}://{authority}" if scheme else authority


def _validate_url(
    raw: str,
    *,
    schemes: set[str],
    label: str,
    require_tls_for_remote: bool = False,
) -> str:
    value = raw.strip()
    if any(char in value for char in "\r\n\0"):
        raise ValueError(f"{label} contains an invalid control character")
    parsed = urlsplit(value)
    if not parsed.scheme or parsed.scheme.lower() not in schemes or not parsed.netloc:
        allowed = ", ".join(sorted(schemes))
        raise ValueError(f"{label} must be a {allowed} URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{label} must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{label} must not contain a query string or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} has an invalid port") from exc
    insecure_scheme = (
        parsed.scheme.lower()
        if require_tls_for_remote and parsed.scheme.lower() in {"http", "redis"}
        else ""
    )
    if insecure_scheme:
        host = (parsed.hostname or "").rstrip(".").lower()
        loopback = host == "localhost"
        if not loopback:
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = False
        if not loopback:
            secure = "HTTPS" if insecure_scheme == "http" else "rediss://"
            raise ValueError(f"{label} must use {secure} outside loopback")
    return value


def _write_config(payload: dict, path: Path | None = None) -> Path:
    """Atomically write the secret-free Codex backend configuration."""
    target = path or _config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.parent.chmod(0o700)
    except OSError:
        pass

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temp_path, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return target


def _child_env(
    *,
    drop_auth_token: bool = False,
    isolate_cloud_auth: bool = False,
) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "CLAUDE_PLUGIN_DATA",
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CONTROL_PLANE_API_URL",
        "KUMIHO_TENANT_HINT",
        "KUMIHO_FIREBASE_API_KEY",
        "KUMIHO_FIREBASE_ID_TOKEN",
        "KUMIHO_FIREBASE_PROJECT_ID",
        "KUMIHO_USE_CONTROL_PLANE_TOKEN",
        "KUMIHO_WORKSPACE_ROOT",
        "KUMIHO_ENV_FILE",
        "KUMIHO_AUTO_CONFIGURE",
        "KUMIHO_DISCOVERY_CACHE_FILE",
        "KUMIHO_CLAUDE_MODE",
        "KUMIHO_CLAUDE_SERVER_ENDPOINT",
        "KUMIHO_LOCAL_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ENDPOINT",
        "KUMIHO_SERVER_ADDRESS",
        "UPSTASH_REDIS_URL",
        "KUMIHO_UPSTASH_REDIS_URL",
        "KUMIHO_LOCAL_REDIS_URL",
        "KUMIHO_MEMORY_PROXY_URL",
        "KUMIHO_MCP_HOSTED",
        "KUMIHO_HOSTED_LOCAL_REDIS",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "KUMIHO_LLM_BASE_URL",
        "KUMIHO_CODEX_BACKEND",
        "KUMIHO_CODEX_CE_ENDPOINT",
        "KUMIHO_CODEX_CE_REDIS_URL",
        "KUMIHO_CODEX_CE_LLM_BASE_URL",
        "KUMIHO_SERVER_USE_TLS",
        "KUMIHO_SERVER_AUTHORITY",
        "KUMIHO_SSL_TARGET_OVERRIDE",
        "KUMIHO_SERVER_CA_FILE",
        "KUMIHO_REQUIRE_TLS",
    ):
        env.pop(key, None)
    # Codex Cloud auth is always loaded from the host-isolated directory by
    # run_kumiho_cloud.py. Never forward an ambient/Claude bearer, even for a
    # non-auth onboarding child.
    env.pop("KUMIHO_AUTH_TOKEN", None)
    env["KUMIHO_CLAUDE_HOST"] = "codex"
    config_root = _config_dir()
    env["KUMIHO_CODEX_CONFIG_ROOT"] = str(config_root)
    env["KUMIHO_CONFIG_DIR"] = str(
        config_root / CODEX_AUTH_DIRNAME if isolate_cloud_auth else config_root
    )
    if isolate_cloud_auth:
        env["KUMIHO_CONTROL_PLANE_URL"] = OFFICIAL_CONTROL_PLANE_URL
        env["KUMIHO_CONTROL_PLANE_API_URL"] = OFFICIAL_CONTROL_PLANE_URL
    return env


def _run(
    command: list[str],
    *,
    timeout: int,
    capture: bool = False,
    drop_auth_token: bool = False,
) -> subprocess.CompletedProcess:
    """Run a non-interactive child with a real process-tree timeout."""
    result = bounded_proc.run(
        command,
        env=_child_env(drop_auth_token=drop_auth_token),
        timeout=timeout,
    )
    if not capture:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
    return result


def _run_interactive(
    command: list[str],
    *,
    timeout: int,
    drop_auth_token: bool = False,
    isolate_cloud_auth: bool = False,
) -> subprocess.CompletedProcess:
    """Run secure Cloud auth on the TTY inside a bounded process tree."""
    return bounded_proc.run(
        command,
        env=_child_env(
            drop_auth_token=drop_auth_token,
            isolate_cloud_auth=isolate_cloud_auth,
        ),
        timeout=timeout,
        stdout=None,
        stderr=None,
    )


def _provision() -> Path | None:
    print("[kumiho-codex] Step 1/5: checking the shared ~/.kumiho/venv runtime...")
    try:
        result = _run(
            [sys.executable, str(PYTHON_LAUNCHER), "--provision"],
            timeout=PROVISION_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print("[kumiho-codex] Provisioning timed out; retry onboarding.", file=sys.stderr)
        return None
    if result.returncode != 0:
        print("[kumiho-codex] Runtime provisioning failed.", file=sys.stderr)
        return None
    venv_python = _venv_python()
    if not venv_python.is_file():
        print(
            f"[kumiho-codex] Provisioning finished but the runtime was not found: "
            f"{venv_python}",
            file=sys.stderr,
        )
        return None
    return venv_python


def _cached_auth_works(
    venv_python: Path,
    *,
    announce: bool = True,
    drop_auth_token: bool = False,
) -> bool:
    try:
        result = _run(
            [str(venv_python), str(CLOUD_RUNNER), "--auth-check"],
            timeout=AUTH_TIMEOUT_S,
            capture=True,
            drop_auth_token=drop_auth_token,
        )
    except subprocess.TimeoutExpired:
        return False
    if result.returncode == 0:
        if announce and result.stdout.strip():
            print(result.stdout.strip())
        return True
    return False


def _configure_cloud(venv_python: Path, *, non_interactive: bool, reauth: bool) -> bool:
    print("[kumiho-codex] Step 2/5: configuring Kumiho Cloud authentication...")
    if not reauth and _cached_auth_works(venv_python):
        pass
    else:
        if non_interactive or not sys.stdin.isatty():
            print(
                "[kumiho-codex] Cloud login requires a secure interactive terminal.\n"
                "Run this command yourself (do not paste credentials into Codex):\n"
                f'  node "{SCRIPT_DIR / "run_kumiho_mcp.mjs"}" --onboard cloud',
                file=sys.stderr,
            )
            return False
        try:
            auth = _auth_executable(venv_python)
            if not auth.is_file():
                print(
                    "[kumiho-codex] The secure authentication command is missing; "
                    "rerun onboarding to repair the runtime.",
                    file=sys.stderr,
                )
                return False
            result = _run_interactive(
                [str(auth), "login"],
                timeout=5 * 60,
                drop_auth_token=reauth,
                isolate_cloud_auth=True,
            )
        except subprocess.TimeoutExpired:
            print("[kumiho-codex] Cloud login timed out.", file=sys.stderr)
            return False
        if result.returncode != 0 or not _cached_auth_works(
            venv_python,
            drop_auth_token=reauth,
        ):
            print("[kumiho-codex] Cloud authentication failed.", file=sys.stderr)
            return False

    path = _write_config({"schema_version": CONFIG_SCHEMA, "backend": "cloud"})
    print(f"[kumiho-codex] Step 3/5: Codex-only backend config written to {path}")
    return True


def _probe_ce(endpoint: str, timeout: float = 2.0) -> bool:
    normalized = _normalize_endpoint(endpoint)
    if "://" in normalized:
        parsed = urlsplit(normalized)
        probe_scheme = "https" if parsed.scheme in {"https", "grpcs"} else "http"
        authority = parsed.netloc
    else:
        probe_scheme, authority = "http", normalized
    try:
        with urllib.request.urlopen(
            f"{probe_scheme}://{authority}/api/_live", timeout=timeout
        ) as response:
            return getattr(response, "status", 200) < 400
    except Exception:
        return False


def _configure_ce(
    args: argparse.Namespace,
    existing: dict | None = None,
) -> tuple[dict, bool]:
    print("[kumiho-codex] Step 2/5: configuring self-hosted Community Edition...")
    previous = existing if existing and existing.get("backend") == "ce" else {}
    endpoint = _normalize_endpoint(
        args.ce_endpoint or previous.get("endpoint") or DEFAULT_CE_ENDPOINT
    )
    redis_url = _validate_url(
        args.ce_redis_url or previous.get("redis_url") or DEFAULT_CE_REDIS_URL,
        schemes={"redis", "rediss"},
        label="CE Redis URL",
        require_tls_for_remote=True,
    )
    payload = {
        "schema_version": CONFIG_SCHEMA,
        "backend": "ce",
        "endpoint": endpoint,
        "redis_url": redis_url,
    }
    llm_base_url = args.ce_llm_base_url or previous.get("llm_base_url")
    if llm_base_url:
        payload["llm_base_url"] = _validate_url(
            llm_base_url,
            schemes={"http", "https"},
            label="CE LLM base URL",
            require_tls_for_remote=True,
        )
    path = _write_config(payload)
    print(f"[kumiho-codex] Step 3/5: Codex-only backend config written to {path}")
    live = _probe_ce(endpoint)
    if live:
        print(f"[kumiho-codex] CE server is live at {endpoint}.")
    else:
        print(
            f"[kumiho-codex] Warning: no CE server answered at {endpoint}; "
            "the configuration was saved for its next start.",
            file=sys.stderr,
        )
    return payload, live


def _ingest_skills(venv_python: Path, backend: str) -> bool:
    print("[kumiho-codex] Step 4/5: ingesting Kumiho skill references...")
    if not INGEST_SCRIPT.is_file():
        print(f"[kumiho-codex] Ingestion helper is missing: {INGEST_SCRIPT}", file=sys.stderr)
        return False
    try:
        result = _run(
            [
                str(venv_python),
                str(INGEST_SCRIPT),
                "--backend",
                backend,
                "--config",
                str(_config_path()),
            ],
            timeout=INGEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print("[kumiho-codex] Skill ingestion timed out.", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(
            "[kumiho-codex] Skill ingestion was not completed; onboarding can "
            "be safely re-run.",
            file=sys.stderr,
        )
        return False
    return True


def _verify_runtime(venv_python: Path) -> bool:
    print("[kumiho-codex] Step 5/5: verifying the MCP runtime...")
    try:
        result = _run(
            [sys.executable, str(PYTHON_LAUNCHER), "--self-test"],
            timeout=PROVISION_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print("[kumiho-codex] MCP self-test timed out.", file=sys.stderr)
        return False
    if result.returncode != 0:
        print("[kumiho-codex] MCP runtime self-test failed.", file=sys.stderr)
        return False
    return venv_python.is_file()


def _verify_backend(venv_python: Path, backend: str) -> bool:
    if not VERIFY_SCRIPT.is_file():
        print(f"[kumiho-codex] Backend verifier is missing: {VERIFY_SCRIPT}", file=sys.stderr)
        return False
    try:
        result = _run(
            [
                str(venv_python),
                str(VERIFY_SCRIPT),
                "--backend",
                backend,
                "--config",
                str(_config_path()),
            ],
            timeout=AUTH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        print("[kumiho-codex] Backend verification timed out.", file=sys.stderr)
        return False
    return result.returncode == 0


def _existing_config() -> dict | None:
    try:
        body = json.loads(_config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("the existing Codex backend config is unreadable") from exc
    if not isinstance(body, dict) or body.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("the existing Codex backend config has an unsupported schema")
    backend = body.get("backend")
    if backend == "cloud":
        return {"schema_version": CONFIG_SCHEMA, "backend": "cloud"}
    if backend == "ce":
        try:
            result = {
                "schema_version": CONFIG_SCHEMA,
                "backend": "ce",
                "endpoint": _normalize_endpoint(str(body.get("endpoint") or "")),
                "redis_url": _validate_url(
                    str(body.get("redis_url") or DEFAULT_CE_REDIS_URL),
                    schemes={"redis", "rediss"},
                    label="CE Redis URL",
                    require_tls_for_remote=True,
                ),
            }
            if body.get("llm_base_url"):
                result["llm_base_url"] = _validate_url(
                    str(body["llm_base_url"]),
                    schemes={"http", "https"},
                    label="CE LLM base URL",
                    require_tls_for_remote=True,
                )
            return result
        except ValueError as exc:
            raise ValueError("the existing Codex CE config is invalid") from exc
    raise ValueError("the existing Codex backend config names an unknown backend")


def _resolve_backend(
    args: argparse.Namespace,
    venv_python: Path,
    existing: dict | None = None,
) -> str:
    if args.backend in {"cloud", "ce"}:
        return args.backend
    if args.reauth:
        return "cloud"
    if args.ce_endpoint or args.ce_redis_url or args.ce_llm_base_url:
        return "ce"
    if existing:
        backend = str(existing["backend"])
        print(f"[kumiho-codex] Reusing the existing {backend.upper()} backend choice.")
        return backend
    if _cached_auth_works(venv_python, announce=False):
        print("[kumiho-codex] Auto-selected Cloud from the valid credential cache.")
        return "cloud"
    if _probe_ce(DEFAULT_CE_ENDPOINT):
        print(f"[kumiho-codex] Auto-selected CE at {DEFAULT_CE_ENDPOINT}.")
        return "ce"
    print("[kumiho-codex] Auto-selected Cloud; secure login may be required.")
    return "cloud"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Onboard Kumiho Memory for Codex without changing Claude config or "
            "putting credentials in command arguments."
        )
    )
    parser.add_argument(
        "backend",
        nargs="?",
        default="auto",
        choices=("auto", "cloud", "ce"),
        help="Backend to configure (default: auto-detect).",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Use cached Cloud auth only; never prompt for credentials.",
    )
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="Force a secure interactive Cloud login (Cloud only).",
    )
    parser.add_argument(
        "--ce-endpoint",
        default=None,
        metavar="HOST:PORT",
        help=f"CE endpoint (default: {DEFAULT_CE_ENDPOINT}).",
    )
    parser.add_argument(
        "--ce-redis-url",
        default=None,
        metavar="URL",
        help=f"CE Redis URL (default: {DEFAULT_CE_REDIS_URL}).",
    )
    parser.add_argument(
        "--ce-llm-base-url",
        default=None,
        metavar="URL",
        help="Optional OpenAI-compatible local LLM base URL.",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip graph skill ingestion while still configuring the backend.",
    )
    parser.add_argument(
        "--config-dir",
        metavar="PATH",
        help="Override the Kumiho config directory (advanced/testing).",
    )
    args = parser.parse_args(argv)
    if args.reauth and args.backend == "ce":
        parser.error("--reauth is valid only with the cloud backend")
    return args


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    args = _parse_args(argv)
    if args.config_dir:
        os.environ["KUMIHO_CONFIG_DIR"] = str(Path(args.config_dir).expanduser())

    print("[kumiho-codex] Kumiho Memory onboarding for Codex")
    print("[kumiho-codex] Credentials are never accepted in chat or command arguments.")

    venv_python = _provision()
    if venv_python is None:
        return 1

    try:
        existing = _existing_config()
    except ValueError as exc:
        explicit_repair = (
            args.backend in {"cloud", "ce"}
            or args.reauth
            or bool(args.ce_endpoint or args.ce_redis_url or args.ce_llm_base_url)
        )
        if not explicit_repair:
            print(
                f"[kumiho-codex] {exc}; auto-selection is disabled so an "
                "explicit CE choice can never silently become Cloud.\n"
                "Run onboarding with an explicit `cloud` or `ce` backend to "
                "repair it.",
                file=sys.stderr,
            )
            return 2
        existing = None
    backend = _resolve_backend(args, venv_python, existing)
    configured_payload: dict | None = None

    try:
        if backend == "cloud":
            configured = _configure_cloud(
                venv_python,
                non_interactive=args.non_interactive,
                reauth=args.reauth,
            )
            if not configured:
                return 2
        else:
            configured_payload, _live = _configure_ce(args, existing)
    except (OSError, ValueError) as exc:
        print(f"[kumiho-codex] Configuration failed: {exc}", file=sys.stderr)
        return 1

    ingested = args.skip_ingest or _ingest_skills(venv_python, backend)

    runtime_ok = _verify_runtime(venv_python)
    backend_ok = _verify_backend(venv_python, backend)
    if not (ingested and runtime_ok and backend_ok):
        print(
            "[kumiho-codex] Onboarding is incomplete; configuration was kept "
            "and the wizard can be safely re-run.",
            file=sys.stderr,
        )
        return 3

    if backend == "ce" and (
        not configured_payload
        or not _probe_ce(str(configured_payload["endpoint"]))
    ):
        print(
            "[kumiho-codex] Onboarding is incomplete; the CE liveness probe failed.",
            file=sys.stderr,
        )
        return 3

    print("[kumiho-codex] Onboarding complete. Start a new Codex session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
