"""``python -m app`` -- the web process. The port comes from the PORT variable."""

from __future__ import annotations

import uvicorn

from app.core.config import get_settings


def main() -> None:
    """Run uvicorn; SIGTERM triggers the lifespan shutdown (sockets close cleanly)."""
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 - containers must listen on all interfaces
        port=settings.port,
        log_config=None,  # our JSON logging owns the handlers
        timeout_graceful_shutdown=settings.shutdown_timeout_seconds,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
