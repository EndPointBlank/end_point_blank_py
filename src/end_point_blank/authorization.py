from __future__ import annotations

import base64
from typing import Optional

from .configuration import Configuration


class Authorization:
    """
    Generates HTTP authorization headers for EndPointBlank API calls.

    If a valid Bearer token can be obtained for the given base URL it returns a
    ``Bearer <token>`` header; otherwise falls back to HTTP Basic auth
    using the configured ``client_id`` and ``client_secret``.

    Equivalent to the Ruby gem's ``EndPointBlank::Authorization``.
    """

    @classmethod
    def header(cls, base_url: Optional[str] = None) -> str:
        """
        Returns a formatted authorization header value.

        :param base_url: The URL you are about to call, with any query string
            and fragment removed. If provided, a token covering it is used (and
            minted if necessary) and returned as a Bearer header. Called with no
            argument this is the Basic form -- which is what the calls to intake
            itself use, since intake already holds this service's credential.
        :returns: ``"Bearer <token>"`` or ``"Basic <credentials>"``
        """
        if base_url:
            # Import here to avoid circular dependency
            from .tokens.access_tokens import AccessTokens
            token = AccessTokens().token(base_url)
            if token:
                return f"Bearer {token}"
        return f"Basic {cls.basic_credentials()}"

    @staticmethod
    def basic_credentials() -> str:
        """Returns the Base64-encoded ``client_id:client_secret`` string."""
        config = Configuration()
        raw = f"{config.client_id}:{config.client_secret}"
        return base64.b64encode(raw.encode()).decode()
