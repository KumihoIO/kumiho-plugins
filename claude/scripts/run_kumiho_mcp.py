#!/usr/bin/env python3
"""Bootstrap and run Kumiho MCP server for Claude plugin environments."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import venv
from pathlib import Path


DEFAULT_PACKAGE_SPEC = "kumiho[mcp]>=0.9.7 kumiho-memory[all]>=0.3.1"
MARKER_FILE = ".installed-packages.txt"
DEFAULT_DISCOVERY_USER_AGENT = "kumiho-claude/0.8.1"


def _state_dir() -> Path:
    override = os.getenv("KUMIHO_CLAUDE_HOME", "").strip()
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "kumiho-claude"

    xdg = os.getenv("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "kumiho-claude"
    return Path.home() / ".cache" / "kumiho-claude"


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run(cmd: list[str], *, check: bool = True) -> int:
    # Redirect stdout → stderr so pip/venv output never pollutes the MCP
    # stdio channel.  Claude Desktop connects stdout directly to its
    # JSON-RPC parser, so any stray text there hangs the connection.
    proc = subprocess.run(cmd, stdout=sys.stderr, check=check)
    return proc.returncode


def _needs_install(python_path: Path, marker_path: Path, package_spec: str) -> bool:
    if not python_path.exists():
        return True

    marker = marker_path.read_text(encoding="utf-8").strip() if marker_path.exists() else ""
    if marker != package_spec:
        return True

    check_code = (
        "import importlib.util,sys;"
        "mods=('kumiho.mcp_server','kumiho_memory');"
        "missing=[m for m in mods if importlib.util.find_spec(m) is None];"
        "sys.exit(1 if missing else 0)"
    )
    try:
        _run([str(python_path), "-c", check_code], check=True)
    except subprocess.CalledProcessError:
        return True
    return False


def _install_dependencies(python_path: Path, package_spec: str) -> None:
    _run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"])
    packages = shlex.split(package_spec) if package_spec else shlex.split(DEFAULT_PACKAGE_SPEC)
    _run([str(python_path), "-m", "pip", "install", "--upgrade", *packages])


def _ensure_runtime() -> Path:
    raw_spec = os.getenv("KUMIHO_CLAUDE_PACKAGE_SPEC", "").strip()
    package_spec = DEFAULT_PACKAGE_SPEC if (not raw_spec or _looks_like_placeholder(raw_spec)) else raw_spec
    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    venv_dir = state_dir / "venv"
    marker_path = state_dir / MARKER_FILE
    python_path = _venv_python(venv_dir)

    if not python_path.exists():
        print(f"[kumiho-claude] Creating virtualenv: {venv_dir}", file=sys.stderr)
        venv.create(venv_dir, with_pip=True)

    if _needs_install(python_path, marker_path, package_spec):
        print("[kumiho-claude] Installing dependencies...", file=sys.stderr)
        _install_dependencies(python_path, package_spec)
        marker_path.write_text(package_spec, encoding="utf-8")

    return python_path


def _warn_auth() -> None:
    auth_token = _load_bearer_token()
    if auth_token:
        return
    print(
        "[kumiho-claude] Warning: KUMIHO_AUTH_TOKEN is not set. "
        "Memory and graph operations will fail until a token is provided.",
        file=sys.stderr,
    )


def _decode_jwt_claims(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8"))
        claims = json.loads(decoded.decode("utf-8"))
        if isinstance(claims, dict):
            return claims
    except Exception:
        return None
    return None


def _validate_auth_token() -> None:
    auth_token = _load_bearer_token()
    if not auth_token:
        return

    claims = _decode_jwt_claims(auth_token)
    if claims:
        return

    print(
        "[kumiho-claude] Warning: KUMIHO_AUTH_TOKEN does not look like a JWT. "
        "Use a dashboard-minted Kumiho API token.",
        file=sys.stderr,
    )


def _load_bearer_token() -> str:
    value = _clean_token_candidate((os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip())
    if _looks_like_placeholder(value):
        value = ""
    if value:
        return value
    return _load_cached_kumiho_token()


def _cached_kumiho_auth_path() -> Path:
    config_dir = (os.getenv("KUMIHO_CONFIG_DIR", "") or "").strip()
    if config_dir:
        return Path(config_dir).expanduser() / "kumiho_authentication.json"
    return Path.home() / ".kumiho" / "kumiho_authentication.json"


def _read_cached_kumiho_credentials() -> dict | None:
    path = _cached_kumiho_auth_path()

    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    return body


def _load_cached_kumiho_token() -> str:
    body = _read_cached_kumiho_credentials()
    if not body:
        return ""

    now = int(time.time())
    # Session tokens (from `kumiho-cli login`) have expiry checks.
    # Dashboard API tokens are long-lived; expiry check is optional.
    candidates = (
        ("control_plane_token", "cp_expires_at"),
        ("id_token", "expires_at"),
        ("api_token", "api_token_expires_at"),
    )
    for token_key, expiry_key in candidates:
        raw = body.get(token_key)
        if not isinstance(raw, str):
            continue
        token = _clean_token_candidate(raw.strip())
        if not token or _looks_like_placeholder(token):
            continue
        expiry = body.get(expiry_key)
        if isinstance(expiry, (int, float)) and int(expiry) <= now + 30:
            continue
        return token
    return ""


def _discovery_token_candidates() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(candidate: str) -> None:
        token = _clean_token_candidate((candidate or "").strip())
        if not token or _looks_like_placeholder(token):
            return
        if token in seen:
            return
        seen.add(token)
        out.append(token)

    add((os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip())
    add(_load_bearer_token())

    body = _read_cached_kumiho_credentials()
    if isinstance(body, dict):
        now = int(time.time())
        for token_key, expiry_key in (("control_plane_token", "cp_expires_at"), ("id_token", "expires_at")):
            raw = body.get(token_key)
            if not isinstance(raw, str):
                continue
            expiry = body.get(expiry_key)
            if isinstance(expiry, (int, float)) and int(expiry) <= now + 30:
                continue
            add(raw)

    return out


def _clean_token_candidate(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        token = token[1:-1].strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _looks_like_placeholder(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    # Guard against unresolved template literals like ${KUMIHO_AUTH_TOKEN:-}
    # being injected as raw strings by a host/plugin runtime.
    return text.startswith("${") and text.endswith("}")


def _set_env_if_absent(key: str, value: str, source: str) -> bool:
    existing = (os.getenv(key, "") or "").strip()
    if existing and not _looks_like_placeholder(existing):
        return False
    candidate = (value or "").strip()
    if key == "KUMIHO_AUTH_TOKEN":
        candidate = _clean_token_candidate(candidate)
    if not candidate or _looks_like_placeholder(candidate):
        return False
    os.environ[key] = candidate
    print(f"[kumiho-claude] Loaded {key} from {source}.", file=sys.stderr)
    return True


def _plugin_root() -> Path:
    from_env = (os.getenv("CLAUDE_PLUGIN_ROOT", "") or "").strip()
    if from_env:
        return Path(from_env).expanduser()
    return Path(__file__).resolve().parents[1]


def _hydrate_env_from_dotenv() -> None:
    """Read KEY=VALUE pairs from .env.local at the plugin root.

    This lets users (and the /kumiho-auth command) drop a simple dotenv file
    next to the plugin without touching .mcp.json.  On Claude Desktop the
    host cannot resolve shell-style ``${VAR:-}`` templates in the env block,
    so .env.local serves as a reliable local override.
    """
    root = _plugin_root()
    for name in (".env.local", ".env"):
        dotenv_path = root / name
        if not dotenv_path.exists():
            continue
        try:
            for line in dotenv_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip optional surrounding quotes
                if len(value) >= 2 and (
                    (value[0] == '"' and value[-1] == '"')
                    or (value[0] == "'" and value[-1] == "'")
                ):
                    value = value[1:-1]
                _set_env_if_absent(key, value, str(dotenv_path))
        except Exception:
            pass
        return  # stop after the first file found


def _hydrate_env_from_plugin_mcp() -> None:
    root = _plugin_root()
    mcp_path = root / ".mcp.json"
    if not mcp_path.exists():
        return

    try:
        body = json.loads(mcp_path.read_text(encoding="utf-8"))
    except Exception:
        return

    if not isinstance(body, dict):
        return
    servers = body.get("mcpServers")
    if not isinstance(servers, dict):
        return
    server = servers.get("kumiho-memory")
    if not isinstance(server, dict):
        return
    env = server.get("env")
    if not isinstance(env, dict):
        return

    for key in ("KUMIHO_AUTH_TOKEN", "KUMIHO_CONTROL_PLANE_URL", "KUMIHO_TENANT_HINT"):
        raw = env.get(key)
        if isinstance(raw, str):
            _set_env_if_absent(key, raw, f"{mcp_path}")


def _candidate_settings_paths() -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    cwd = Path.cwd().resolve()
    for base in [cwd, *cwd.parents]:
        claude_dir = base / ".claude"
        add(claude_dir / "settings.local.json")
        add(claude_dir / "settings.json")

    home_claude = Path.home() / ".claude"
    add(home_claude / "settings.local.json")
    add(home_claude / "settings.json")
    return candidates


def _hydrate_env_from_claude_settings() -> None:
    candidates = _candidate_settings_paths()
    found_any = False
    for settings_path in candidates:
        if not settings_path.exists():
            continue
        try:
            body = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(body, dict):
            continue
        env = body.get("env")
        if not isinstance(env, dict):
            continue
        found_any = True
        loaded_any = False
        for key in ("KUMIHO_AUTH_TOKEN", "KUMIHO_CONTROL_PLANE_URL", "KUMIHO_TENANT_HINT"):
            raw = env.get(key)
            if isinstance(raw, str):
                if _set_env_if_absent(key, raw, f"{settings_path}"):
                    loaded_any = True
        if loaded_any:
            return
    if not found_any:
        print(
            f"[kumiho-claude] Searched {len(candidates)} settings paths; "
            "none contained a usable env block. "
            "Use /kumiho-auth or set KUMIHO_AUTH_TOKEN in ~/.kumiho/kumiho_authentication.json.",
            file=sys.stderr,
        )


def _hydrate_env_from_local_config() -> None:
    _hydrate_env_from_dotenv()
    _hydrate_env_from_claude_settings()
    _hydrate_env_from_plugin_mcp()
    env_auth = (os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip()
    cached = _load_bearer_token()
    if cached and (not env_auth or _looks_like_placeholder(env_auth)):
        os.environ["KUMIHO_AUTH_TOKEN"] = cached
        print(
            "[kumiho-claude] Loaded KUMIHO_AUTH_TOKEN from local Kumiho credential cache.",
            file=sys.stderr,
        )


def _claude_desktop_config_paths() -> list[Path]:
    """Return platform-specific Claude Desktop global config paths.

    On Windows MSIX installs, Claude Desktop reads from a virtualised
    path under LocalAppData\\Packages instead of the standard %APPDATA%
    location.  We check the MSIX path first, then the standard path.
    """
    paths: list[Path] = []
    if os.name == "nt":
        # MSIX virtualised path (Windows Store / official installer).
        local_appdata = os.getenv("LOCALAPPDATA", "")
        if local_appdata:
            msix_base = Path(local_appdata) / "Packages"
            if msix_base.exists():
                for entry in msix_base.iterdir():
                    if entry.name.startswith("Claude_") and entry.is_dir():
                        candidate = (
                            entry / "LocalCache" / "Roaming" / "Claude"
                            / "claude_desktop_config.json"
                        )
                        paths.append(candidate)
                        break
        # Standard (non-MSIX) path.
        appdata = os.getenv("APPDATA", "")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        paths.append(
            Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
        xdg_config = os.getenv("XDG_CONFIG_HOME", "")
        if xdg_config:
            paths.append(Path(xdg_config) / "Claude" / "claude_desktop_config.json")
        else:
            paths.append(Path.home() / ".config" / "Claude" / "claude_desktop_config.json")
    return paths


def _try_sync_token_to_config(config_path: Path, token: str) -> bool:
    """Attempt to write *token* into a single MCP config file.

    Returns True on success, False on any error.
    """
    if not config_path.exists():
        return False

    try:
        body = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    servers = body.get("mcpServers")
    if not isinstance(servers, dict):
        return False

    server = None
    for name in ("kumiho-memory", "kumiho"):
        entry = servers.get(name)
        if isinstance(entry, dict):
            server = entry
            break
    if server is None:
        return False

    env = server.get("env")
    if not isinstance(env, dict):
        return False

    current = (env.get("KUMIHO_AUTH_TOKEN") or "").strip()
    if current == token:
        return True  # already in sync

    env["KUMIHO_AUTH_TOKEN"] = token
    try:
        config_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        print(
            f"[kumiho-claude] Synced KUMIHO_AUTH_TOKEN into {config_path.name}.",
            file=sys.stderr,
        )
        return True
    except Exception:
        return False


def _bootstrap_desktop_server_entries() -> None:
    """Ensure Claude Desktop configs have a kumiho-memory server entry.

    Writes absolute paths (no ``${...}`` templates) so Claude Desktop can
    launch the server without shell variable resolution.  Called on every
    startup so the config self-heals if the entry was wiped or never created.

    Uses the actual running script location (``__file__``) rather than
    ``CLAUDE_PLUGIN_ROOT`` env because the env variable may point to a
    non-versioned or stale path.
    """
    # Always derive plugin root from the actual running script, not env.
    plugin_root = Path(__file__).resolve().parents[1]
    script_path = plugin_root / "scripts" / "run_kumiho_mcp.py"
    if not script_path.exists():
        return  # Not in a standard plugin layout; skip.

    venv_py = _venv_python(_state_dir() / "venv")
    command = str(venv_py) if venv_py.exists() else sys.executable

    server_entry: dict = {
        "command": command,
        "args": [str(script_path)],
        "env": {
            "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        },
    }
    # Include the resolved token if one is available so Claude Desktop picks
    # it up immediately on the next restart (no extra /kumiho-auth step needed).
    token = _clean_token_candidate((os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip())
    if token and not _looks_like_placeholder(token):
        server_entry["env"]["KUMIHO_AUTH_TOKEN"] = token

    for desktop_path in _claude_desktop_config_paths():
        try:
            if desktop_path.exists():
                body = json.loads(desktop_path.read_text(encoding="utf-8"))
                if not isinstance(body, dict):
                    body = {}
            else:
                body = {}
        except Exception:
            continue

        # Check if already configured *with a valid script path*.
        # If the entry exists but points to a missing file (e.g. stale version
        # path), fall through and overwrite it with the correct paths.
        def _has_valid_entry(b: dict) -> bool:
            servers = b.get("mcpServers")
            if not isinstance(servers, dict):
                return False
            for name in ("kumiho-memory", "kumiho"):
                entry = servers.get(name)
                if not isinstance(entry, dict):
                    continue
                args = entry.get("args") or []
                if args and Path(args[0]).exists():
                    return True
            return False

        if _has_valid_entry(body):
            continue  # Valid entry — skip.

        # Not configured — bootstrap the entry.
        body.setdefault("mcpServers", {})["kumiho-memory"] = server_entry
        try:
            desktop_path.parent.mkdir(parents=True, exist_ok=True)
            desktop_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
            print(
                f"[kumiho-claude] Bootstrapped kumiho-memory server entry in {desktop_path.name}.",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"[kumiho-claude] Could not write {desktop_path}: {exc}",
                file=sys.stderr,
            )


def _sync_token_to_mcp_json() -> None:
    """Write the resolved token into MCP config so Claude Desktop picks it up.

    Tries the plugin-local ``.mcp.json`` first.  If that fails (read-only
    filesystem), falls back to the Claude Desktop global config.
    """
    token = _clean_token_candidate((os.getenv("KUMIHO_AUTH_TOKEN", "") or "").strip())
    if not token or _looks_like_placeholder(token):
        return

    # Try plugin-local .mcp.json first
    if _try_sync_token_to_config(_plugin_root() / ".mcp.json", token):
        return

    # Fallback: Claude Desktop global config
    for desktop_path in _claude_desktop_config_paths():
        if _try_sync_token_to_config(desktop_path, token):
            return

    print(
        "[kumiho-claude] Warning: could not sync token to any MCP config file.",
        file=sys.stderr,
    )


def _build_discovery_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/discovery/tenant"):
        return base
    if base.endswith("/api/discovery"):
        return f"{base}/tenant"
    if base.endswith("/api"):
        return f"{base}/discovery/tenant"
    return f"{base}/api/discovery/tenant"


def _load_control_plane_url() -> str:
    raw = (os.getenv("KUMIHO_CONTROL_PLANE_URL", "") or "").strip()
    if _looks_like_placeholder(raw):
        raw = ""
    return raw or "https://control.kumiho.cloud"


def _load_discovery_user_agent() -> str:
    raw = (os.getenv("KUMIHO_CLAUDE_DISCOVERY_USER_AGENT", "") or "").strip()
    if not raw or _looks_like_placeholder(raw):
        return DEFAULT_DISCOVERY_USER_AGENT
    return raw


def _normalize_server_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if not target:
        return None

    if "://" in target:
        parsed = urllib.parse.urlparse(target)
        if not parsed.hostname:
            return None
        scheme = parsed.scheme.lower()
        port = parsed.port
        if port is None:
            if scheme in {"https", "grpcs"}:
                port = 443
            elif scheme in {"http", "grpc"}:
                port = 80
            else:
                port = 443
        return f"{parsed.hostname}:{port}"

    if "/" in target:
        target = target.split("/", 1)[0]
    return target or None


def _bootstrap_server_endpoint() -> None:
    preset_endpoint = os.getenv("KUMIHO_SERVER_ENDPOINT", "").strip() or os.getenv("KUMIHO_SERVER_ADDRESS", "").strip()
    if preset_endpoint:
        print(
            "[kumiho-claude] Ignoring pre-set KUMIHO_SERVER_ENDPOINT/KUMIHO_SERVER_ADDRESS; "
            "resolving endpoint via control-plane discovery.",
            file=sys.stderr,
        )
    # Always clear any inherited endpoint so startup cannot lock onto stale routing.
    os.environ.pop("KUMIHO_SERVER_ENDPOINT", None)
    os.environ.pop("KUMIHO_SERVER_ADDRESS", None)

    token_candidates = _discovery_token_candidates()
    if not token_candidates:
        print(
            "[kumiho-claude] KUMIHO_AUTH_TOKEN is not set; skipping discovery bootstrap. "
            "MCP tools will load, but authenticated calls will fail until token is provided.",
            file=sys.stderr,
        )
        # Set a sentinel endpoint so the SDK does NOT fall back to
        # localhost:8080.  The .invalid TLD is guaranteed to never
        # resolve (RFC 6761), producing a clear "not connected" error.
        os.environ["KUMIHO_SERVER_ENDPOINT"] = "needs-auth.kumiho.invalid:443"
        return

    control_plane_url = _load_control_plane_url()
    discovery_url = _build_discovery_url(control_plane_url)
    tenant_hint = os.getenv("KUMIHO_TENANT_HINT", "").strip()
    discovery_user_agent = _load_discovery_user_agent()

    payload: dict[str, str] = {}
    if tenant_hint:
        payload["tenant_hint"] = tenant_hint

    body_text: str | None = None
    last_error: Exception | None = None
    request_body = json.dumps(payload).encode("utf-8")

    for index, bearer in enumerate(token_candidates, start=1):
        request = urllib.request.Request(
            discovery_url,
            data=request_body,
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
                "User-Agent": discovery_user_agent,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                body_text = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = ""
            detail = detail.strip().replace("\n", " ")
            if detail:
                detail = f" {detail[:160]}"
            print(
                f"[kumiho-claude] Discovery candidate #{index} failed ({exc.code}).{detail}",
                file=sys.stderr,
            )
            last_error = exc
        except Exception as exc:
            print(
                f"[kumiho-claude] Discovery candidate #{index} request error: {exc}",
                file=sys.stderr,
            )
            last_error = exc

    if body_text is None:
        if last_error is None:
            raise RuntimeError("Control-plane discovery failed with no usable token candidates.")
        raise RuntimeError(f"Control-plane discovery failed across all token candidates: {last_error}")

    try:
        body = json.loads(body_text)
    except json.JSONDecodeError:
        raise RuntimeError("Control-plane discovery returned invalid JSON.")

    region = body.get("region")
    if not isinstance(region, dict):
        raise RuntimeError("Control-plane discovery response missing region routing.")

    raw_target = ""
    grpc_authority = region.get("grpc_authority")
    if isinstance(grpc_authority, str) and grpc_authority.strip():
        raw_target = grpc_authority
    else:
        server_url = region.get("server_url")
        if isinstance(server_url, str) and server_url.strip():
            raw_target = server_url

    resolved_target = _normalize_server_target(raw_target)
    if not resolved_target:
        raise RuntimeError("Control-plane discovery response missing gRPC target.")

    os.environ["KUMIHO_SERVER_ENDPOINT"] = resolved_target
    os.environ.pop("KUMIHO_SERVER_ADDRESS", None)
    print(
        f"[kumiho-claude] Resolved KUMIHO_SERVER_ENDPOINT={resolved_target} via discovery bootstrap.",
        file=sys.stderr,
    )


def _sanitize_placeholder_env_vars() -> None:
    """Strip unresolved ${VAR:-default} placeholders that Claude Desktop
    passes through as literal strings.  Without this, downstream code
    (pip install, log-level parsing, etc.) receives garbage values.
    """
    for key in (
        "KUMIHO_AUTH_TOKEN",
        "KUMIHO_CONTROL_PLANE_URL",
        "KUMIHO_CLAUDE_PACKAGE_SPEC",
        "KUMIHO_CLAUDE_DISCOVERY_USER_AGENT",
        "KUMIHO_MCP_LOG_LEVEL",
        "KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK",
    ):
        raw = (os.getenv(key, "") or "").strip()
        if raw and _looks_like_placeholder(raw):
            os.environ.pop(key, None)
            print(
                f"[kumiho-claude] Cleared unresolved placeholder for {key}.",
                file=sys.stderr,
            )


def _configure_llm_fallback() -> None:
    if os.getenv("KUMIHO_CLAUDE_DISABLE_LLM_FALLBACK", "").strip().lower() in {"1", "true", "yes"}:
        return

    key_vars = ("KUMIHO_LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    if any(os.getenv(var, "").strip() for var in key_vars):
        return

    os.environ.setdefault("KUMIHO_LLM_PROVIDER", "openai")
    os.environ.setdefault("OPENAI_API_KEY", "kumiho-claude-fallback")
    os.environ.setdefault("KUMIHO_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    print(
        "[kumiho-claude] No LLM API key detected. "
        "Using fail-fast local fallback for summarization.",
        file=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Kumiho MCP with auto-bootstrap.")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Provision runtime and verify required modules, then exit.",
    )
    args, passthrough = parser.parse_known_args()

    _sanitize_placeholder_env_vars()
    _hydrate_env_from_local_config()
    _bootstrap_desktop_server_entries()
    _sync_token_to_mcp_json()
    _validate_auth_token()
    _warn_auth()
    try:
        _bootstrap_server_endpoint()
    except RuntimeError as exc:
        # Prevent SDK from falling back to localhost:8080.
        os.environ["KUMIHO_SERVER_ENDPOINT"] = "needs-auth.kumiho.invalid:443"
        print(
            "[kumiho-claude] Discovery bootstrap failed. "
            "Run /kumiho-auth to set up authentication. "
            f"Error: {exc}",
            file=sys.stderr,
        )
    _configure_llm_fallback()
    python_path = _ensure_runtime()

    if args.self_test:
        check_code = (
            "import importlib.util,sys;"
            "mods=('kumiho.mcp_server','kumiho_memory');"
            "missing=[m for m in mods if importlib.util.find_spec(m) is None];"
            "print('ok' if not missing else 'missing:' + ','.join(missing));"
            "sys.exit(0 if not missing else 1)"
        )
        return _run([str(python_path), "-c", check_code], check=False)

    cmd = [str(python_path), "-m", "kumiho.mcp_server", *passthrough]
    # On Windows os.execv spawns a new process and immediately exits the
    # current one.  Claude Desktop monitors the original PID; when it exits
    # the transport is closed ~85 ms later even though the child is still
    # running.  subprocess.run keeps this process alive (waiting) so Claude
    # Desktop never detects a premature exit.  stdin/stdout/stderr are
    # inherited by the child automatically (no redirection needed).
    if os.name == "nt":
        proc = subprocess.run(cmd)
        return proc.returncode
    # On POSIX, true exec replaces the process image in-place (same PID).
    os.execv(str(python_path), cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
