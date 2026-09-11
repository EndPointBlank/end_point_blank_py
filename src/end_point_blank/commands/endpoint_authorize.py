from __future__ import annotations

import logging
from typing import Any, Dict, NamedTuple, Optional

import requests as req_lib

from ..authorization import Authorization
from ..configuration import Configuration
from ._http import post
from .authentication_cache import AuthenticationCache
from ..request_store import RequestStore
from ..base_url import hostname as resolve_hostname

logger = logging.getLogger(__name__)


class _Grant(NamedTuple):
    """What a granted authorization carries forward to the request it decided.

    This is the cached value, so a cache hit records exactly what a miss does.
    A tuple is never ``None``, the one value the cache declines to store, so a
    grant with no deprecation (or no id) is cached like any other.
    """

    source_application_environment_id: Optional[str]
    deprecation: Optional[dict]


class _CachedResponse:
    """Minimal response-like object returned on a cache hit."""
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class EndpointAuthorize:
    """
    Authorizes an incoming request by sending its details to the EndPointBlank authorize API.

    Sends the request path, HTTP method, client authorization header, application name,
    API version, source IP, and target hostname to the configured ``authorize_url``.

    A grant records the caller's source application environment and any deprecation
    block on the current request (see :class:`~end_point_blank.request_store.RequestStore`).

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
        # Host header only, never the forwarded chain -- see base_url.hostname.
        # The old `host_header.split(":")[0]` turned "[::1]:8443" into "[".
        server_name = resolve_hostname(environ)

        # The version is part of the key because authorization is decided per
        # endpoint version, and so is the deprecation carried back with it.
        # Without it, two callers on different versions of the same route share
        # one entry.
        cache_key = f"epb_auth:{client_auth}:{path}:{method}:{config.app_name}:{version}"
        cache = AuthenticationCache()
        # One read rather than exists() then retrieve(): an entry that expired
        # between the two would be a hit with no grant in it.
        cached = cache.retrieve(cache_key)
        if cached is not None:
            logger.debug("Authorization cache hit for %s %s", method, path)
            # The cached value is the grant -- the caller's source environment
            # and the deprecation block -- not a truthy flag. Authorization is
            # cached per client+route, so caching a flag would name the caller
            # and send the Deprecation and Sunset headers on cache misses alone —
            # roughly one request in N, which reads as a flaky feature rather
            # than a missing one.
            _record(cached)
            return _CachedResponse(201)

        body: Dict[str, Any] = {
            "path": path,
            "http_method": method,
            "client_auth": client_auth,
            "target_hostname": server_name,
            "application": config.app_name,
            "endpoint_version": version,
            "source_ip": _remote_addr(environ),
            "uuid": RequestStore.get_uuid(),
        }

        # Basic, not Bearer. This call is to intake, which already holds this
        # service's credential -- minting a token to present it back was a hop
        # that bought nothing. With no Bearer there is no stale token, so the
        # 401 retry that used to live here is gone: a 401 now means the
        # credential is wrong, which is worth surfacing rather than retrying.
        response = post(config.authorize_url, Authorization.header(), body)

        if response is None:
            return None

        logger.info("Authorization response: %s - %s", response.status_code, response.text)
        if response.status_code == 201:
            grant = _grant_from(response)
            _record(grant)
            cache.store(cache_key, grant)
        elif response.status_code > 299:
            logger.error("Authorization failed: %s - %s", response.status_code, response.text)
        return response


def _record(grant: _Grant) -> None:
    """Stash the grant on the current request: the writers stamp the caller's
    source environment on its response, log and error rows, and the middleware
    turns the deprecation block into headers."""
    RequestStore.set_source_application_environment_id(grant.source_application_environment_id)
    RequestStore.set_deprecation(grant.deprecation)


def _grant_from(response) -> _Grant:
    try:
        payload = response.json()
    except Exception:
        payload = None

    return _Grant(
        source_application_environment_id=_source_environment_id_from(payload, response),
        deprecation=_deprecation_from(payload),
    )


def _source_environment_id_from(payload, response) -> Optional[str]:
    """``data[0].source_application_environment_id``: the application environment
    intake resolved for the service calling this one.

    Intake renders the grant list under ``data`` (``IntakeWeb.AuthorizationJSON.show/1``),
    and the Rails SDK reads it from there. This SDK read nothing from it, so every
    response, log and error row went to intake naming no caller (sc-473). There
    is no fallback to any other key -- the Elixir SDK read ``accesses``, which
    intake has never sent (sc-463) -- because a fallback would only hide the next
    mismatch.

    Intake refuses (401) any caller whose credential has no application
    environment, so a 201 without the id means the contract moved. The request
    still proceeds: refusing would turn a reporting defect into an outage of
    legitimate traffic. But it logs, once per cache miss, rather than passing for
    success.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    first = data[0] if isinstance(data, list) and data else None
    found = first.get("source_application_environment_id") if isinstance(first, dict) else None
    if isinstance(found, str) and found:
        return found

    logger.error(
        "Authorization granted without data[0].source_application_environment_id, so "
        "this request's responses, logs and errors will not name their caller: %s",
        response.text,
    )
    return None


def _deprecation_from(payload) -> Optional[dict]:
    """The authorize response carries a ``deprecation`` block only when the
    version being called is deprecated. Absent, malformed, or unparseable all
    mean the same thing here: nothing to say."""
    block = payload.get("deprecation") if isinstance(payload, dict) else None
    return block if isinstance(block, dict) else None


def _remote_addr(environ: Dict[str, Any]) -> Optional[str]:
    forwarded = environ.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return environ.get("REMOTE_ADDR")
