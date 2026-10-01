"""
The part of a caller's URL the SDK may keep, send to intake, or log (sc-1469).

Imports nothing from the package, like
:mod:`~end_point_blank.tokens.token_result`, so every layer of the
``Authorization`` -> ``AccessTokens`` -> ``GenerateAccessToken`` cycle can
import it at module level.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit


# Dropped from the stripped form, as Ruby's ``URI#default_port`` drops it, so
# ``https://h:443/x`` and ``https://h/x`` are one cache and failure key.
_DEFAULT_PORTS = {"http": 80, "https": 443}


def strip_url(value) -> Optional[str]:
    """Returns *value* reduced to scheme, host (IPv6 in brackets), port when
    it is not the scheme's default, and path -- or ``None`` when it cannot be,
    or is not an http or https URL.

    The caller controls the URL passed to ``Authorization.header(url)``, and
    its userinfo, query or fragment can carry a secret. intake needs none of
    them: its base-URL normalizer refuses a URL carrying any of them, even an
    empty ``?`` or ``#``, and answers 422. Sending them would leak them to
    intake and guarantee the mint fails, so they are removed before the URL
    reaches the token request, a cache or failure key, a log line or an error.

    Built from the parsed parts rather than by splitting the string, so an
    empty ``?`` or ``#`` cannot slip through. Scheme and host are lowercased
    and the port is dropped when it is empty or the scheme's default (``:80``
    for http, ``:443`` for https); the path keeps the caller's spelling --
    intake owns normalization.

    Only http and https are accepted, because only they can name a provider;
    any other scheme (``ftp``, ``ws``, ``file``, ``mailto`` ...) is refused
    rather than rebuilt into something intake would be asked to mint for.

    Matches the Ruby gem's ``EndPointBlank::TargetUrl.strip`` except that the
    host is lowercased here and kept as written there; intake lowercases it
    either way.

    :returns: The stripped URL, or ``None`` when *value* is not a non-empty
        string, does not parse (a non-numeric port, or one outside 1..65535,
        included), is not http or https, or has no host. ``None`` means no
        request may be made for it.
    """
    if not isinstance(value, str) or value == "":
        return None
    try:
        # Surrounding whitespace is dropped, as a WHATWG URL parser does.
        parts = urlsplit(value.strip())
        port = parts.port  # Raises ValueError for a port that is not a port.
    except ValueError:
        return None
    # Both already lowercase: urlsplit lowercases the scheme, and hostname is
    # the host lowercased, without userinfo, port or IPv6 brackets.
    scheme, host = parts.scheme, parts.hostname
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if port == 0:
        # urlsplit refuses only ports above 65535; 0 is no port to call.
        return None
    if ":" in host:
        host = f"[{host}]"
    # An empty port (``https://h:/x``) parses as None and is dropped with it.
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"
    return f"{scheme}://{host}{parts.path}"
