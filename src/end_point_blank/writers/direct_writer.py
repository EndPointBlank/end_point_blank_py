from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..authorization import Authorization
from ..configuration import Configuration
from ..commands._http import post

logger = logging.getLogger(__name__)

_URL_KEYS = {
    "application_errors_url": lambda c: c.application_errors_url,
    "endpoint_error_url": lambda c: c.endpoint_error_url,
    "log_url": lambda c: c.log_url,
    "requests_url": lambda c: c.requests_url,
    "responses_url": lambda c: c.responses_url,
}


class DirectWriter:
    """
    Synchronously POSTs a list of payloads to the EndPointBlank API.

    Equivalent to the Ruby gem's ``EndPointBlank::Writers::DirectWriter``.
    """

    def __init__(self, url_key: str) -> None:
        self._url_key = url_key
        resolver = _URL_KEYS.get(url_key)
        if resolver is None:
            logger.warning("Unknown URL key '%s', using application_errors_url", url_key)
            resolver = _URL_KEYS["application_errors_url"]
        self._url = resolver(Configuration())

    def write(self, payloads: List[Dict[str, Any]]) -> None:
        """Sends *payloads* as a JSON body to the API endpoint."""
        response = post(self._url, Authorization.header(), {"payload": payloads})
        if response is not None and response.status_code > 299:
            logger.warning("Write failed: %s - %s", response.status_code, response.text)
