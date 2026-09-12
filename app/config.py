"""Runtime settings, read exclusively from environment variables."""

from __future__ import annotations

import os
import secrets
import socket
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


DEFAULT_DATA_DIR = "/data"
DEFAULT_TLS_CERT = "certs/server.crt"   # relative to DATA_DIR
DEFAULT_TLS_KEY = "certs/server.key"


@dataclass(frozen=True)
class Settings:
    admin_token: str
    session_secret: str
    session_ttl_hours: int = 12
    data_dir: str = DEFAULT_DATA_DIR
    discovery_timeout_seconds: int = 5
    host: str = "0.0.0.0"
    port: int = 8000
    tls_enabled: bool = True
    tls_cert_file: str = ""
    tls_key_file: str = ""
    tls_custom_paths: bool = False
    tls_hostnames: tuple[str, ...] = field(default_factory=tuple)
    tls_cert_days: int = 3650
    tls_regenerate: bool = False

    def __post_init__(self) -> None:
        # Direct construction (tests, scripts) may leave the paths empty: derive the defaults.
        if not self.tls_cert_file:
            object.__setattr__(self, "tls_cert_file", os.path.join(self.data_dir, DEFAULT_TLS_CERT))
        if not self.tls_key_file:
            object.__setattr__(self, "tls_key_file", os.path.join(self.data_dir, DEFAULT_TLS_KEY))

    @property
    def config_path(self) -> str:
        return os.path.join(self.data_dir, "config.json")

    @property
    def scheme(self) -> str:
        return "https" if self.tls_enabled else "http"


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{key} must be true or false, got {raw!r}")


def _int(env: dict[str, str], key: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if value < minimum or (maximum is not None and value > maximum):
        bound = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ConfigError(f"{key} must be {bound}, got {value}")
    return value


def default_tls_hostnames() -> tuple[str, ...]:
    names = ["localhost", "127.0.0.1"]
    try:
        host = socket.gethostname().strip()
    except OSError:
        host = ""
    if host and host not in names:
        names.append(host)
    return tuple(names)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build Settings from the environment.

    ADMIN_TOKEN is mandatory and has no default: the container must be started
    with it, otherwise startup fails with a clear message.
    """
    env = dict(os.environ) if env is None else env

    admin_token = env.get("ADMIN_TOKEN", "").strip()
    if not admin_token:
        raise ConfigError(
            "ADMIN_TOKEN environment variable is required. "
            "Start the container with -e ADMIN_TOKEN=<your-secret> "
            "(or set it in .env for docker compose)."
        )

    data_dir = env.get("DATA_DIR", "").strip() or DEFAULT_DATA_DIR

    cert_file = env.get("TLS_CERT_FILE", "").strip()
    key_file = env.get("TLS_KEY_FILE", "").strip()
    if bool(cert_file) != bool(key_file):
        raise ConfigError("TLS_CERT_FILE and TLS_KEY_FILE must be set together")
    custom = bool(cert_file)
    if not custom:
        cert_file = os.path.join(data_dir, DEFAULT_TLS_CERT)
        key_file = os.path.join(data_dir, DEFAULT_TLS_KEY)

    raw_hosts = env.get("TLS_HOSTNAMES", "")
    hostnames = tuple(h.strip() for h in raw_hosts.split(",") if h.strip()) or default_tls_hostnames()

    return Settings(
        admin_token=admin_token,
        session_secret=env.get("SESSION_SECRET", "").strip() or secrets.token_urlsafe(32),
        session_ttl_hours=_int(env, "SESSION_TTL_HOURS", 12, 1),
        data_dir=data_dir,
        discovery_timeout_seconds=_int(env, "DISCOVERY_TIMEOUT_SECONDS", 5, 1),
        host=env.get("HOST", "").strip() or "0.0.0.0",
        port=_int(env, "PORT", 8000, 1, 65535),
        tls_enabled=_bool(env, "TLS_ENABLED", True),
        tls_cert_file=cert_file,
        tls_key_file=key_file,
        tls_custom_paths=custom,
        tls_hostnames=hostnames,
        tls_cert_days=_int(env, "TLS_CERT_DAYS", 3650, 1, 36500),
        tls_regenerate=_bool(env, "TLS_REGENERATE", False),
    )
