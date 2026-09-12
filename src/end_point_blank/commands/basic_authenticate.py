from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests as req_lib

from ..authorization import Authorization
from ..configuration import Configuration
from ..request_store import RequestStore
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

        # These are the names intake reads on POST /api/authorize, and the same
        # ones EndpointAuthorize sends to that same URL. http_method is not
        # optional: every clause of AuthorizeAccess.authorize/1 pattern-matches
        # it, so a body without it is answered 401 invalid_params no matter what
        # credential it carried.
        body: Dict[str, Any] = {
            "path": path,
            "http_method": method,
            "client_auth": client_auth,
            "application": config.app_name,
            "endpoint_version": version,
            "source_ip": ip_address or _remote_addr(environ),
        }

        response = post(config.authorize_url, Authorization.header(), body)
        if response is None:
            return None

        logger.info("Authentication response: %s - %s", response.status_code, response.text)
        if response.status_code == 201:
            RequestStore.set_source_application_environment_id(_source_environment_id_from(response))
        if response.status_code > 299:
            logger.error("Authentication failed: %s - %s", response.status_code, response.text)
        return response


def _source_environment_id_from(response) -> Optional[str]:
    try:
        payload = response.json()
    except Exception as exc:
        logger.error(
            "Authenticated, but the authorize response has no "
            "data[0].source_application_environment_id, so this request's responses, "
            "logs and errors will not name their caller: %s",
            exc,
        )
        return None

    data = payload.get("data") if isinstance(payload, dict) else None
    first = data[0] if isinstance(data, list) and data else None
    found = first.get("source_application_environment_id") if isinstance(first, dict) else None
    if isinstance(found, str) and found:
        return found

    logger.error(
        "Authenticated, but the authorize response has no "
        "data[0].source_application_environment_id, so this request's responses, "
        "logs and errors will not name their caller: body=%s",
        payload,
    )
    return None
