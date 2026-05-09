from __future__ import annotations

import base64
from typing import Optional

from .configuration import Configuration


class Authorization:
    """
    Generates HTTP authorization headers for EndPointBlank API calls.

    If a valid Bearer token exists for the given hostname it returns a
    ``Bearer <token>`` header; otherwise falls back to HTTP Basic auth
    using the configured ``client_id`` and ``client_secret``.

    Equivalent to the Ruby gem's ``EndPointBlank::Authorization``.
    """

    @classmethod
    def header(cls, hostname: Optional[str] = None) -> str:
        """
        Returns a formatted authorization header value.

        :param hostname: If provided and a valid token is cached for this
            hostname, returns a Bearer token header.
        :returns: ``"Bearer <token>"`` or ``"Basic <credentials>"``
        """
        if hostname:
            # Import here to avoid circular dependency
            from .tokens.access_tokens import AccessTokens
            token = AccessTokens().token(hostname)
            if token:
                return f"Bearer {token}"
        return f"Basic {cls.basic_credentials()}"

    @staticmethod
    def basic_credentials() -> str:
        """Returns the Base64-encoded ``client_id:client_secret`` string."""
        config = Configuration()
        raw = f"{config.client_id}:{config.client_secret}"
        return base64.b64encode(raw.encode()).decode()
