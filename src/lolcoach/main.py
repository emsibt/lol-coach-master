"""Application entrypoint.

The single place where logging is configured, then the FastAPI app is built.
Run with::

    uvicorn lolcoach.main:app --reload
"""

from __future__ import annotations

import logging

from lolcoach.config import get_settings, load_env
from lolcoach.logging_setup import configure_logging
from lolcoach.server.app import create_app

load_env()
_settings = get_settings()
configure_logging(_settings.service, _settings.log_level)

logger = logging.getLogger(__name__)
logger.info("LoL Coach API configured", extra={"data": {"log_level": _settings.log_level}})

app = create_app()
