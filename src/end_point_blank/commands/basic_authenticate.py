from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests as req_lib

from ..authorization import Authorization
from ..configuration import Configuration
from ._http import post
from .endpoint_authorize import _remote_addr

logger = logging.getLogger(__name__)


class BasicAuthenticate:
    """
    Authenticates an incoming request by sending its details to the EndPointBlank
    authorize API using Basic auth credentials.

    Equivalent to the Ruby gem's ``EndPointBlank::Commands::BasicAuthenticate``.
    """

    @staticmethod
    def authenticate(
        environ: Dict[str, Any],
        path: str,
        version: Optional[str],
        ip_address: Optional[str] = None,
    ) -> Optional[req_lib.Response]:
        """
        Sends request details to the authorization endpoint for validation.

        :param environ: The WSGI environ dict for the current request.
        :param path: The route pattern path.
        :param version: The detected API version (may be ``None``).
        :param ip_address: Override for the client IP; falls back to ``REMOTE_ADDR``.
        :returns: The HTTP response, or ``None`` on error.
        """
        config = Configuration()
        client_auth = environ.get("HTTP_AUTHORIZATION")
        method = environ.get("REQUEST_METHOD")
        path_info = environ.get("PATH_INFO", "")

        logger.info(
            "Authenticating request: %s %s with client_auth: %s",
            method, path_info, client_auth,
        )

        body: Dict[str, Any] = {
            "path": path,
            "action": method,
            "client_auth": client_auth,
            "application": config.app_name,
            "version": version,
            "ip_address": ip_address or _remote_addr(environ),
        }

        response = post(config.authorize_url, Authorization.header(), body)
        if response is None:
            return None

        logger.info("Authentication response: %s - %s", response.status_code, response.text)
        if response.status_code > 299:
            logger.error("Authentication failed: %s - %s", response.status_code, response.text)
        return response
