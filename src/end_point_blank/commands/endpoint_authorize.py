from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests as req_lib

from ..authorization import Authorization
from ..configuration import Configuration
from ._http import post

logger = logging.getLogger(__name__)


class EndpointAuthorize:
    """
    Authorizes an incoming request by sending its details to the EndPointBlank authorize API.

    Sends the request path, HTTP method, client authorization header, application name,
    API version, source IP, and target hostname to the configured ``authorize_url``.

    Equivalent to the Ruby gem's ``EndPointBlank::Commands::EndpointAuthorize``.
    """

    @staticmethod
    def authorize(
        environ: Dict[str, Any],
        path: str,
        version: Optional[str],
    ) -> Optional[req_lib.Response]:
        """
        Sends the WSGI environ details to the authorization endpoint.

        :param environ: The WSGI environ dict for the current request.
        :param path: The route pattern path (e.g. ``/api/v1/users``).
        :param version: The detected API version (may be ``None``).
        :returns: The HTTP response, or ``None`` on error.
        """
        config = Configuration()
        client_auth = environ.get("HTTP_AUTHORIZATION")
        server_name = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "")

        body: Dict[str, Any] = {
            "path": path,
            "http_method": environ.get("REQUEST_METHOD"),
            "client_auth": client_auth,
            "target_hostname": server_name,
            "application": config.app_name,
            "endpoint_version": version,
            "source_ip": _remote_addr(environ),
        }

        response = post(config.authorize_url, Authorization.header(), body)
        if response is None:
            return None

        logger.info("Authorization response: %s - %s", response.status_code, response.text)
        if response.status_code > 299:
            logger.error("Authorization failed: %s - %s", response.status_code, response.text)
        return response


def _remote_addr(environ: Dict[str, Any]) -> Optional[str]:
    forwarded = environ.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return environ.get("REMOTE_ADDR")
