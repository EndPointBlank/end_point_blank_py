from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..authorization import Authorization
from ..configuration import Configuration
from ._http import post

logger = logging.getLogger(__name__)


class GenerateAccessToken:
    """
    Generates an access token by calling the EndPointBlank API.

    Sends the hostname (and optional TTL) to the configured ``access_token_url``
    and returns the parsed response dict containing ``token`` and ``expired_at``.

    Equivalent to the Ruby gem's ``EndPointBlank::Commands::GenerateAccessToken``.
    """

    @staticmethod
    def token(hostname: str) -> Optional[Dict[str, Any]]:
        """
        Requests a new access token for *hostname*.

        :returns: A dict with ``token`` and ``expired_at``, or ``None`` on failure.
        """
        config = Configuration()
        body: Dict[str, Any] = {"hostname": hostname}
        if config.token_ttl is not None:
            body["token_ttl"] = config.token_ttl

        response = post(config.access_token_url, Authorization.header(), body)
        if response is None:
            return None

        logger.info("Access token response: %s", response.status_code)
        try:
            return response.json()
        except Exception as exc:
            logger.error("Failed to parse access token response: %s", exc)
            return None
