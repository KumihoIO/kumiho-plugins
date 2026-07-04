"""Local state: config file, OAuth signing key, PIN, and derived URLs.

Everything lives under ``~/.kumiho/gpt`` with restrictive permissions. This is
a single-user, local tool — the PIN and signing key are local secrets, treated
like the SSH keys they resemble.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# Defaults. Both bind to loopback; only the tunnel is public.
DEFAULT_GATEWAY_PORT = 8790   # the OAuth-aware gateway (tunnel points here)
DEFAULT_INNER_PORT = 8791     # mcp-proxy wrapping stdio kumiho-mcp
DEFAULT_MCP_PATH = "/mcp"     # streamable-HTTP MCP mount on the gateway
PIN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no ambiguous chars (0/O, 1/I/L)


def config_dir() -> Path:
    override = (os.getenv("KUMIHO_GPT_HOME", "") or "").strip()
    base = Path(override).expanduser() if override else (Path.home() / ".kumiho" / "gpt")
    base.mkdir(parents=True, exist_ok=True)
    _harden(base)
    return base


def _harden(path: Path) -> None:
    """Restrict to the current user: chmod 700/600 on POSIX, icacls on Windows.

    These files hold the PIN, the tunnel token, and the OAuth signing key, so
    on multi-user machines they must not be world-readable.
    """
    if os.name == "nt":
        _harden_windows(path)
        return
    try:
        mode = stat.S_IRWXU if path.is_dir() else (stat.S_IRUSR | stat.S_IWUSR)
        path.chmod(mode)
    except OSError:
        pass


def _harden_windows(path: Path) -> None:
    """Break inheritance and grant only the current user access (best effort)."""
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if not user:
        try:
            import getpass

            user = getpass.getuser()
        except Exception:
            user = ""
    if not user:
        # Do not silently leave secrets at inherited ACLs — surface it.
        import sys

        print(
            f"[kumiho-gpt-connect] WARNING: could not resolve the current user to "
            f"restrict {path}; secrets may inherit broad ACLs. Set USERNAME.",
            file=sys.stderr,
        )
        return
    grant = f"{user}:(OI)(CI)F" if path.is_dir() else f"{user}:F"
    try:
        import subprocess

        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            check=False, capture_output=True,
        )
    except Exception:
        pass


def _config_path() -> Path:
    return config_dir() / "config.json"


def signing_key_path() -> Path:
    return config_dir() / "oauth_signing_key.pem"


def clients_path() -> Path:
    return config_dir() / "oauth_clients.json"


@dataclass
class Config:
    # Backend the gateway talks to underneath: "ce" (tokenless loopback) or
    # "cloud" (paid, managed — the local installer only points at it).
    backend: str = "ce"
    # Tunnel provider: "cloudflare" | "ngrok".
    tunnel_provider: str = "cloudflare"
    # Cloudflare tunnel token OR ngrok authtoken (never printed back in full).
    tunnel_token: str = ""
    # Public base URL the tunnel exposes, e.g. https://memory.example.com.
    # Filled in once `serve` brings the tunnel up (ngrok is dynamic; cloudflare
    # is usually a stable named hostname the user configures).
    public_base_url: str = ""
    # Stable cloudflare hostname when the user pre-configured a named tunnel.
    cloudflare_hostname: str = ""
    gateway_port: int = DEFAULT_GATEWAY_PORT
    inner_port: int = DEFAULT_INNER_PORT
    mcp_path: str = DEFAULT_MCP_PATH
    # One-time PIN the user types on the ChatGPT OAuth consent screen.
    pin: str = ""
    # Random gateway identity used as the OAuth token `kid` / issuer salt.
    instance_id: str = field(default_factory=lambda: secrets.token_hex(8))

    @property
    def issuer(self) -> str:
        """OAuth issuer = the public origin (AS lives on the gateway)."""
        return self.public_base_url.rstrip("/")

    @property
    def connector_url(self) -> str:
        """The URL a user pastes into ChatGPT's custom-connector field."""
        if not self.public_base_url:
            return ""
        return f"{self.public_base_url.rstrip('/')}{self.mcp_path}"


def load() -> Config:
    path = _config_path()
    if not path.exists():
        return Config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Config()
    known = {f for f in Config.__dataclass_fields__}  # type: ignore[attr-defined]
    return Config(**{k: v for k, v in data.items() if k in known})


def save(cfg: Config) -> None:
    path = _config_path()
    path.write_text(json.dumps(asdict(cfg), indent=2) + "\n", encoding="utf-8")
    _harden(path)


def new_pin(length: int = 8) -> str:
    return "".join(secrets.choice(PIN_ALPHABET) for _ in range(length))


def ensure_pin(cfg: Config) -> str:
    if not cfg.pin:
        cfg.pin = new_pin()
        save(cfg)
    return cfg.pin
