from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests as req_lib

from ..authorization import Authorization
from ..configuration import Configuration
from ._http import post
from .authentication_cache import AuthenticationCache

logger = logging.getLogger(__name__)


class _CachedResponse:
    """Minimal response-like object returned on a cache hit."""
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class EndpointAuthorize:
    """
    Authorizes an incoming request by sending its details to the EndPointBlank authorize API.

    Sends the request path, HTTP method, client authorization header, application name,
    API version, source IP, and target hostname to the configured ``authorize_url``.

    Successful authorization results are cached for ``Configuration().cache_ttl`` seconds
    (default 300 s) keyed on (client_auth, path, method) to avoid a live intake call on
    every request.

    Equivalent to the Ruby gem's ``EndPointBlank::Commands::EndpointAuthorize``.
    """

    @staticmethod
    def authorize(
        environ: Dict[str, Any],
        path: str,
        version: Optional[str],
    ) -> Optional[req_lib.Response]:
        config = Configuration()
        client_auth = environ.get("HTTP_AUTHORIZATION", "")
        method = environ.get("REQUEST_METHOD", "")
        server_name = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "")

        cache_key = f"epb_auth:{client_auth}:{path}:{method}:{config.app_name}"
        cache = AuthenticationCache()
        if cache.exists(cache_key):
            logger.debug("Authorization cache hit for %s %s", method, path)
            return _CachedResponse(201)

        body: Dict[str, Any] = {
            "path": path,
            "http_method": method,
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
        if response.status_code == 201:
            cache.store(cache_key, True)
        elif response.status_code > 299:
            logger.error("Authorization failed: %s - %s", response.status_code, response.text)
        return response


def _remote_addr(environ: Dict[str, Any]) -> Optional[str]:
    forwarded = environ.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return environ.get("REMOTE_ADDR")
