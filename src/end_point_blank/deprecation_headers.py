"""
Formats the deprecation facts returned by an authorize call into the standard
response headers.

- ``Deprecation`` — RFC 9745, an Item Structured Header Date: ``@1688169599``
- ``Sunset`` — RFC 8594, an HTTP-date: ``Sat, 31 Dec 2018 23:59:59 GMT``

RFC 9745 permits a past value ("was deprecated at that date"), which is what
EndPointBlank emits: deprecation takes effect when it is declared.

Pure and stateless on purpose. The SDK does not know what a lifecycle is — it
relays two timestamps the portal already decided about, and this turns them into
two strings. That keeps the shared vectors assertable without a request.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEPRECATION = "Deprecation"
SUNSET = "Sunset"

# Fixed English abbreviations. strftime's %a/%b are locale-sensitive, and an
# HTTP-date is the same in every locale — a server running under a different
# LC_TIME must not emit "sam., 31 déc. 2018".
_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _parse(value) -> Optional[datetime]:
    """Parse a timestamp, returning ``None`` for anything unusable.

    Types are matched rather than coerced. ``datetime.fromtimestamp(12345)`` is
    a valid date in 1970, so accepting a number would turn a nonsense value into
    a plausible-looking header — the one outcome worse than no header at all.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if not isinstance(value, str) or not value:
        return None

    try:
        # fromisoformat handles "+00:00" but not the "Z" our API emits.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def deprecation_value(moment: datetime) -> str:
    """``@1688169599`` — no quotes, no sub-second precision."""
    return f"@{int(moment.timestamp())}"


def sunset_value(moment: datetime) -> str:
    """``Sat, 31 Dec 2018 23:59:59 GMT`` — zero-padded day, always GMT."""
    utc = moment.astimezone(timezone.utc)
    return (
        f"{_DAYS[utc.weekday()]}, {utc.day:02d} {_MONTHS[utc.month - 1]} "
        f"{utc.year:04d} {utc.hour:02d}:{utc.minute:02d}:{utc.second:02d} GMT"
    )


def build(deprecation: Optional[dict]) -> Dict[str, str]:
    """Header name to value for a deprecation block; empty when there is
    nothing to say."""
    if not isinstance(deprecation, dict):
        return {}

    headers: Dict[str, str] = {}

    deprecated_at = _parse(deprecation.get("deprecated_at"))
    if deprecated_at:
        headers[DEPRECATION] = deprecation_value(deprecated_at)

    sunset_at = _parse(deprecation.get("sunset_at"))
    if sunset_at:
        headers[SUNSET] = sunset_value(sunset_at)

    return headers


def apply(
    response_headers: List[Tuple[str, str]], deprecation: Optional[dict]
) -> List[Tuple[str, str]]:
    """Returns *response_headers* with the deprecation headers appended.

    Never raises into the provider's response path: a malformed timestamp is a
    bug worth no header, not a 500 on a request that already succeeded. Existing
    headers of the same name are left alone — an application that sets its own
    ``Sunset`` has said something more specific than we know.
    """
    try:
        existing = {name.lower() for name, _ in response_headers}
        extra = [
            (name, value)
            for name, value in build(deprecation).items()
            if name.lower() not in existing
        ]
        return list(response_headers) + extra
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to build deprecation headers: %s", exc)
        return response_headers
