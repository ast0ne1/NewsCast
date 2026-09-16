"""Process entry: serve NewsCast over HTTP or HTTPS based on settings."""

from __future__ import annotations

import logging
import sys

import uvicorn

from app.config import env
from app.db import SessionLocal, init_db
from app.services import settings, tls

logger = logging.getLogger("newscast.serve")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    db = SessionLocal()
    try:
        if settings.https_enabled(db):
            try:
                tls.ensure_certificate(db)
                ssl_certfile, ssl_keyfile = tls.ssl_file_paths()
            except Exception:
                logger.exception(
                    "HTTPS is enabled but the certificate could not be prepared; refusing to start without TLS"
                )
                sys.exit(1)
            logger.info("Serving HTTPS on %s:%s", env.host, env.port)
        else:
            logger.info("Serving HTTP on %s:%s", env.host, env.port)
    finally:
        db.close()

    uvicorn.run(
        "app.main:app",
        host=env.host,
        port=env.port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )


if __name__ == "__main__":
    main()
