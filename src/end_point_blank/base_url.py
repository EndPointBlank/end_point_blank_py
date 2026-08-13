"""
Resolves the base URL the caller used -- scheme, host and port -- from a WSGI
environ.

This deliberately reads the environ directly rather than going through any
framework helper. Rack, Express, the servlet spec and Plug each resolve "host"
differently (Rack takes the last X-Forwarded-Host hop, Express the first, WSGI
and Plug neither), which is why the same request produced five different
answers across the five clients. Reading the environ ourselves is the only way
they can agree.

Forwarded headers are honored when ``trust_proxy_headers`` is on, which it is by
default, taking the LAST hop: ``host`` was already caller-controlled in every
client, and behind a proxy that appends, the last value is the proxy's own
observation rather than anything the caller planted. A directly-exposed
deployment can pass ``trust_proxy_headers=False`` and get scheme, host and port
from the connection and the ``Host`` header only.

The flag arrives as an argument rather than being read from ``Configuration``
here, so that this module stays framework- and configuration-free and both
states are directly testable.

A forwarded header counts as evidence only once its last hop parses to
something usable for its own field. One that is blank, whitespace-only, or
malformed is indistinguishable from an absent header: it is ignored entirely
and the corresponding fallback runs exactly as if it had never been sent.
This applies independently to all three of X-Forwarded-Proto,
X-Forwarded-Host and X-Forwarded-Port.

Only a validly-shaped X-Forwarded-Proto or X-Forwarded-Port marks the request
as proxied for the purpose of suppressing the connection's own scheme and
port. X-Forwarded-Host does not: ``host`` has always been caller-controlled
through the ``Host`` header regardless of proxy trust, so seeing it forwarded
proves nothing new about whether the connection's scheme and port are still
trustworthy. Host is resolved on its own, independent track.

A port is never synthesized from a scheme that failed to resolve: without a
scheme there is no default port to compare against, and guessing would let
the same origin be reported two different ways depending on which one header
a proxy happened to send. If the scheme is unresolved, the port -- however it
was sourced -- is omitted.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

_HOSTNAME = re.compile(r"\A[a-z0-9._-]+\Z")
_IPV6 = re.compile(r"\A\[[0-9a-f:.]+\]\Z")
_SCHEME = re.compile(r"\A[a-z][a-z0-9+.-]{0,31}\Z")
_DIGITS = re.compile(r"\A[0-9]+\Z")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MAX_HOST_BYTES = 253  # DNS's hard cap on a hostname's total length.


def resolve(environ: Any, trust_proxy_headers: bool = True) -> Dict[str, Any]:
    """Return only the fields that resolved to a usable value.

    A field that could not be resolved is absent, never ``None``: the receiver
    has to be able to tell "this SDK did not report a port" from "the port is
    null".

    With ``trust_proxy_headers=False`` the three ``X-Forwarded-*`` headers are
    not read at all, so the request is never treated as proxied and the
    connection's scheme and port stay evidence.
    """
    if not isinstance(environ, dict):
        return {}

    forwarded_proto_raw = (
        _last_hop(environ.get("HTTP_X_FORWARDED_PROTO")) if trust_proxy_headers else None
    )
    forwarded_host_raw = (
        _last_hop(environ.get("HTTP_X_FORWARDED_HOST")) if trust_proxy_headers else None
    )
    forwarded_port_raw = (
        _last_hop(environ.get("HTTP_X_FORWARDED_PORT")) if trust_proxy_headers else None
    )

    # Validate each forwarded header on its own terms. An invalid one collapses
    # to None here -- the same as if the header had never been sent -- so it
    # can never swallow a later fallback via a truthy-but-garbage value.
    forwarded_scheme = _clean_scheme(forwarded_proto_raw)
    forwarded_host_part, forwarded_authority_port = _split_authority(forwarded_host_raw)
    forwarded_host = _clean_host(forwarded_host_part)
    forwarded_port = _valid_port(forwarded_port_raw)

    # Only Proto/Port evidence marks the request as proxied. Host is resolved
    # independently below and never gates this.
    proxied = forwarded_scheme is not None or forwarded_port is not None

    scheme = forwarded_scheme or (None if proxied else _clean_scheme(environ.get("wsgi.url_scheme")))

    if forwarded_host is not None:
        host = forwarded_host
        authority_port = forwarded_authority_port
    else:
        # Either no X-Forwarded-Host was sent, or the one that was sent didn't
        # parse to a usable hostname -- both fall back to Host/SERVER_NAME the
        # same way.
        host_part, authority_port = _split_authority(_host_authority(environ))
        host = _clean_host(host_part)

    port = (
        _clean_port(
            forwarded_port or authority_port or (None if proxied else environ.get("SERVER_PORT")),
            scheme,
        )
        if scheme
        else None
    )

    resolved: Dict[str, Any] = {}
    if scheme:
        resolved["scheme"] = scheme
    if host:
        resolved["host"] = host
    if port:
        resolved["port"] = port
    return resolved


def hostname(environ: Any) -> Optional[str]:
    """The hostname alone, for the authorize path.

    Deliberately not ``resolve(environ)["host"]``: this reads the ``Host``
    header only, never the forwarded chain, whatever ``trust_proxy_headers``
    is set to. The value feeds ``target_hostname``, and the portal resolves an
    application environment from it -- a value matching no registered row is a
    hard 422 with no fallback, not a cache miss. So this path takes the one view
    of the host that cannot change under
    a proxy the deployment does not control, and gives up the proxy's more
    accurate answer to get it.

    Composed from the same ``_split_authority``/``_clean_host`` pair
    ``resolve`` uses, so IPv6 bracketing, lowercasing, and shape and length
    validation are identical between the two; only the authority's source
    differs.
    """
    if not isinstance(environ, dict):
        return None

    host_part, _authority_port = _split_authority(_host_authority(environ))
    return _clean_host(host_part)


def _last_hop(value: Any) -> Optional[str]:
    # A proxy that appends writes its own observation last. A proxy that
    # overwrites (nginx, Caddy, ALB) emits one value, where first and last are
    # the same thing.
    if not isinstance(value, str):
        return None
    hops = [hop.strip() for hop in value.split(",") if hop.strip()]
    return hops[-1] if hops else None


def _host_authority(environ: Any) -> Optional[str]:
    """The authority to read the host from: the ``Host`` header, else the server name.

    An empty ``Host`` header is treated as absent, not as a present-but-unusable
    value. A caller that sends ``Host:`` with no value has said nothing about
    which host it meant, so there is nothing there to prefer over the server
    name. The server name is a server-side value the caller cannot steer, so
    falling through to it concedes no control the caller did not already have.

    On the authorize path the alternative is worse than cosmetic: resolving to
    ``None`` there drops the request to Basic auth and skips the token mint,
    whereas falling through yields a usable application-environment lookup key.

    Python, Java and JS already fell through, because "" is falsy in all three;
    Ruby and Elixir stopped, because "" is truthy in both. One expression
    written five times, diverging only on the empty case. It now lives at one
    site per SDK, and this comment is why.
    """
    host_header = environ.get("HTTP_HOST")
    if host_header:
        return host_header
    return environ.get("SERVER_NAME")


def _split_authority(value: Any) -> Tuple[Optional[str], Optional[str]]:
    # "api.example.com:8443" -> ("api.example.com", "8443")
    # "[2001:db8::1]:8443"   -> ("[2001:db8::1]", "8443")
    if not isinstance(value, str):
        return None, None
    authority = value.strip()
    if authority.startswith("["):
        close = authority.find("]")
        if close == -1:
            return None, None
        rest = authority[close + 1:]
        return authority[: close + 1], rest[1:] if rest.startswith(":") else None
    if authority.count(":") == 1:
        host, _, port = authority.partition(":")
        return host, port
    return authority, None


def _clean_scheme(value: Any) -> Optional[str]:
    # Normalize, then validate. "HTTPS" and "https:" both have to reach intake
    # as "https": JS's location.protocol and Node's URL#protocol keep the colon,
    # nothing pins the case, and intake never rewrites a stored row -- two
    # spellings of one scheme would split the grouping forever. One trailing
    # colon is removed, not all of them, so "https::" still fails the shape
    # check rather than sneaking through (``rstrip(":")`` would let it).
    if not isinstance(value, str):
        return None
    scheme = value.strip().lower()
    if scheme.endswith(":"):
        scheme = scheme[:-1]
    return scheme if _SCHEME.match(scheme) else None


def _clean_host(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    host = value.strip().lower()
    if not host:
        return None
    if not (_HOSTNAME.match(host) or _IPV6.match(host)):
        return None
    # DNS caps a hostname at 253 bytes; nothing longer can be real. The
    # receiving column is varchar(255), and dropping the field beats
    # truncating it -- a truncated hostname still looks like a plausible,
    # wrong one, and the portal reads this value verbatim.
    if len(host.encode("utf-8")) > _MAX_HOST_BYTES:
        return None
    return host


def _valid_port(value: Any) -> Optional[int]:
    # Shape and range only -- no default-port awareness. Used both to decide
    # whether a forwarded port counts as proxy evidence, and as the first
    # stage of _clean_port below.
    if value is None:
        return None
    raw = str(value).strip()
    if not _DIGITS.match(raw):
        return None
    port = int(raw)
    if port < 1 or port > 65535:
        return None
    return port


def _clean_port(value: Any, scheme: Optional[str]) -> Optional[int]:
    port = _valid_port(value)
    if port is None:
        return None
    if _DEFAULT_PORTS.get(scheme) == port:
        return None
    return port
