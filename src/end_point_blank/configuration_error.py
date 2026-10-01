"""
The error raised when a setting the SDK cannot work without is missing.

Imports nothing from the package, like
:mod:`~end_point_blank.tokens.token_result`, so every layer of the
``Authorization`` -> ``AccessTokens`` -> ``GenerateAccessToken`` cycle can
import it at module level.
"""


class ConfigurationError(Exception):
    """
    Raised when a setting the SDK cannot work without is missing -- today, an
    unset or empty ``client_id`` or ``client_secret`` when the SDK builds the
    Basic header for a call to its own intake (sc-1469).

    Loud on purpose. Interpolating the missing values would quietly send
    ``Basic Tm9uZTpOb25l`` (``None:None``) on every call, which intake rejects
    with a 401 -- a misconfiguration dressed up as a revoked credential, and
    reported as :attr:`~end_point_blank.tokens.token_result.TokenOutcome.CREDENTIAL_REJECTED`
    with the advice to re-issue it. Nothing is sent when this is raised.

    Equivalent to the Ruby gem's ``EndPointBlank::ConfigurationError``.
    """
