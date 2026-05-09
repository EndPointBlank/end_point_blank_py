from __future__ import annotations

import logging
import socket
from typing import Any, Dict, List, Optional

from ..authorization import Authorization
from ..configuration import Configuration
from ._http import post

logger = logging.getLogger(__name__)

VERSION = "0.1.0"


class EndpointUpdate:
    """
    Sends application endpoint registration information to the EndPointBlank API.

    Collects metadata (name, hostname, environment, version) along with the
    endpoint list and POSTs it to the configured ``endpoint_update_url``.

    Callers are responsible for providing the endpoint list since Python has no
    universal route-introspection mechanism. For Flask apps use
    :func:`~end_point_blank.flask.endpoint_registrar.register_flask_endpoints`.

    Equivalent to the Ruby gem's ``EndPointBlank::Commands::EndpointUpdate``.
    """

    def __init__(self, endpoints: Optional[List[Dict[str, Any]]] = None) -> None:
        self.endpoints = endpoints or []

    def update(self) -> None:
        """Sends the endpoint registration payload to the API."""
        self._write(self._application_info())

    @classmethod
    def send_update(cls, endpoints: Optional[List[Dict[str, Any]]] = None) -> None:
        """Convenience class method."""
        cls(endpoints).update()

    def _write(self, data: Dict[str, Any]) -> None:
        config = Configuration()
        response = post(config.endpoint_update_url, Authorization.header(), data)
        if response is None:
            return
        if response.status_code > 299:
            logger.error("Failed to update endpoints: %s - %s", response.status_code, response.text)
        else:
            logger.info("Endpoints updated successfully: %s", response.status_code)

    def _application_info(self) -> Dict[str, Any]:
        config = Configuration()
        return {
            "application": config.app_name,
            "hostname": self._hostname(),
            "lib_version": VERSION,
            "environment": config.environment,
            "endpoints": self.endpoints,
            "app_version": config.application_version,
        }

    @staticmethod
    def _hostname() -> str:
        try:
            return socket.gethostname()
        except Exception:
            return "localhost"
