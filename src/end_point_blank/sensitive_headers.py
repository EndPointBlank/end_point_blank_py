"""Headers the SDK never sends to intake (sc-1470).

A request record used to carry every inbound header, so a caller's
``Authorization: Basic client_id:secret`` or bearer token, a proxy credential
and its session cookie landed in the provider's request log unless the
provider had written a masking rule for them. A response record likewise
carried ``Set-Cookie``. The writers drop these before masking runs, rather
than mask them, so no rule and no ``mask_hook`` can bring them back.

One list serves both records: ``Set-Cookie`` never arrives on a request and
the other three are not response headers.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

#: Lower-cased names of the headers left out of every request and response record.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
    }
)


def is_sensitive(name: Any) -> bool:
    """Whether a header called ``name``, in any letter case, is never sent."""
    return str(name).lower() in SENSITIVE_HEADERS


def without_sensitive_headers(headers: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """A new dict of ``headers`` without any of :data:`SENSITIVE_HEADERS`.

    Never mutates its argument; ``None`` or an empty map is ``{}``.
    """
    if not headers:
        return {}
    return {name: value for name, value in headers.items() if not is_sensitive(name)}
