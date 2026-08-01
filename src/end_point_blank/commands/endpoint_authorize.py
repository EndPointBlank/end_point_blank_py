from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests as req_lib

from ..authorization import Authorization
from ..configuration import Configuration
from ._http import post
from .authentication_cache import AuthenticationCache
from ..request_store import RequestStore

# The cache declines to store falsy values, so "authorized, not deprecated"
# needs a truthy marker of its own — otherwise every such request would miss the
# cache and re-authorize.
_NO_DEPRECATION = object()
from ..tokens.access_tokens import AccessTokens

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
        # HTTP_HOST includes the port for non-default ports (e.g.
        # "localhost:3002"). Intake's app-env lookup matches on hostname only,
        # so strip the port to match the Ruby gem's `request.host` behavior.
        host_header = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "")
        server_name = host_header.split(":")[0]

        cache_key = f"epb_auth:{client_auth}:{path}:{method}:{config.app_name}"
        cache = AuthenticationCache()
        if cache.exists(cache_key):
            logger.debug("Authorization cache hit for %s %s", method, path)
            # The cached value is the deprecation block (or the "none" marker),
            # not a truthy flag. Authorization is cached per client+route, so
            # caching a flag would limit the Deprecation and Sunset headers to
            # cache misses — roughly one request in N, which reads as a flaky
            # feature rather than a missing one.
            cached = cache.retrieve(cache_key)
            RequestStore.set_deprecation(None if cached is _NO_DEPRECATION else cached)
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

        auth = Authorization.header(server_name)
        response = post(config.authorize_url, auth, body)

        if response is not None and response.status_code == 401 and auth.startswith("Bearer "):
            AccessTokens().remove(server_name)
            auth = Authorization.header(server_name)
            response = post(config.authorize_url, auth, body)

        if response is None:
            return None

        logger.info("Authorization response: %s - %s", response.status_code, response.text)
        if response.status_code == 201:
            deprecation = _deprecation_from(response)
            RequestStore.set_deprecation(deprecation)
            cache.store(cache_key, deprecation if deprecation else _NO_DEPRECATION)
        elif response.status_code > 299:
            logger.error("Authorization failed: %s - %s", response.status_code, response.text)
        return response


def _deprecation_from(response) -> Optional[dict]:
    """The authorize response carries a ``deprecation`` block only when the
    version being called is deprecated. Absent, malformed, or unparseable all
    mean the same thing here: nothing to say."""
    try:
        payload = response.json()
    except Exception:
        return None

    block = payload.get("deprecation") if isinstance(payload, dict) else None
    return block if isinstance(block, dict) else None


def _remote_addr(environ: Dict[str, Any]) -> Optional[str]:
    forwarded = environ.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return environ.get("REMOTE_ADDR")
