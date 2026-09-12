"""Container entrypoint: load settings, ensure the TLS certificate, start uvicorn.

    python -m app.serve
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from app.config import ConfigError, load_settings
from app.main import create_app
from app.tls import ensure_certificate

log = logging.getLogger("app.serve")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(levelname)s: %(message)s")
    try:
        settings = load_settings()
        tls = ensure_certificate(settings)
    except ConfigError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    kwargs = {}
    if tls:
        kwargs = {"ssl_certfile": tls[0], "ssl_keyfile": tls[1]}
    log.info("Listening on %s://%s:%d (TLS %s)", settings.scheme, settings.host, settings.port,
             "enabled" if tls else "disabled")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                log_level=os.environ.get("LOG_LEVEL", "info").lower(), **kwargs)


if __name__ == "__main__":
    main()
