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


def strip_url(value) -> Optional[str]:
    """Returns *value* reduced to scheme, host (IPv6 in brackets), port when
    one was written, and path -- or ``None`` when it cannot be.

    The caller controls the URL passed to ``Authorization.header(url)``, and
    its userinfo, query or fragment can carry a secret. intake needs none of
    them: its base-URL normalizer refuses a URL carrying any of them, even an
    empty ``?`` or ``#``, and answers 422. Sending them would leak them to
    intake and guarantee the mint fails, so they are removed before the URL
    reaches the token request, a cache or failure key, a log line or an error.

    Built from the parsed parts rather than by splitting the string, so an
    empty ``?`` or ``#`` cannot slip through. Host and path keep the caller's
    spelling -- intake owns normalization.

    The same helper, with the same behaviour, exists in the JS, Java, Elixir
    and Ruby SDKs.

    :returns: The stripped URL, or ``None`` when *value* is not a non-empty
        string, does not parse (a non-numeric or out-of-range port included),
        or has no scheme or host. ``None`` means no request may be made for it.
    """
    if not isinstance(value, str) or value == "":
        return None
    try:
        # Surrounding whitespace is dropped, as a WHATWG URL parser does.
        parts = urlsplit(value.strip())
        parts.port  # Raises ValueError for a port that is not a port.
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    host_and_port = parts.netloc.rpartition("@")[2]
    return f"{parts.scheme}://{host_and_port}{parts.path}"
