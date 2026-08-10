"""
``base_url`` resolves what the caller used, not what the process sees.

This module reads the WSGI environ directly rather than going through any
framework helper. WSGI has no notion of a trusted proxy at all, and the four
sibling clients each had a different one -- which is exactly why the same
request produced five different hosts.
"""

from end_point_blank.base_url import resolve


def environ(**overrides):
    base = {
        "wsgi.url_scheme": "https",
        "SERVER_NAME": "api.example.com",
        "SERVER_PORT": "8443",
        "HTTP_HOST": "API.Example.com:8443",
    }
    base.update(overrides)
    return base


def test_resolves_scheme_host_and_port_from_a_direct_request():
    assert resolve(environ()) == {"scheme": "https", "host": "api.example.com", "port": 8443}


def test_omits_the_port_when_it_is_the_scheme_default():
    assert resolve(environ(HTTP_HOST="api.example.com", SERVER_PORT="443")) == {
        "scheme": "https",
        "host": "api.example.com",
    }


def test_reports_what_the_caller_used_not_what_the_process_sees():
    resolved = resolve(
        environ(
            **{
                "wsgi.url_scheme": "http",
                "SERVER_PORT": "8080",
                "HTTP_HOST": "internal.svc:8080",
                "HTTP_X_FORWARDED_PROTO": "https",
                "HTTP_X_FORWARDED_HOST": "api.example.com",
                "HTTP_X_FORWARDED_PORT": "443",
            }
        )
    )

    assert resolved == {"scheme": "https", "host": "api.example.com"}


def test_omits_the_connection_port_once_a_proxy_is_in_front():
    # 8080 is the internal listener. The caller never saw it, so reporting it
    # would be worse than reporting nothing.
    resolved = resolve(
        environ(
            **{
                "wsgi.url_scheme": "http",
                "SERVER_PORT": "8080",
                "HTTP_HOST": "api.example.com",
                "HTTP_X_FORWARDED_PROTO": "https",
            }
        )
    )

    assert resolved == {"scheme": "https", "host": "api.example.com"}


def test_takes_the_last_forwarded_hop_so_a_caller_cannot_prepend_its_own():
    # A proxy that appends writes its own observation last; a value the caller
    # planted arrives to the left of it.
    resolved = resolve(
        environ(
            HTTP_X_FORWARDED_PROTO="https, http",
            HTTP_X_FORWARDED_HOST="evil.example, api.example.com",
        )
    )

    assert resolved["scheme"] == "http"
    assert resolved["host"] == "api.example.com"


def test_omits_a_field_it_cannot_resolve_rather_than_reporting_null():
    assert resolve({}) == {}


def test_drops_a_host_that_is_not_shaped_like_a_hostname():
    resolved = resolve(environ(HTTP_HOST="api.example.com/../evil?x=1"))

    assert "host" not in resolved
    assert resolved == {"scheme": "https", "port": 8443}


def test_ignores_the_forwarded_headers_when_proxy_headers_are_not_trusted():
    # Same request as test_reports_what_the_caller_used..., resolved both ways,
    # so the only difference between the two expectations is the flag. Off, the
    # request is not proxied at all, so 8080 is evidence again.
    proxied = environ(
        **{
            "wsgi.url_scheme": "http",
            "SERVER_PORT": "8080",
            "HTTP_HOST": "internal.svc:8080",
            "HTTP_X_FORWARDED_PROTO": "https",
            "HTTP_X_FORWARDED_HOST": "api.example.com",
            "HTTP_X_FORWARDED_PORT": "443",
        }
    )

    assert resolve(proxied, trust_proxy_headers=True) == {
        "scheme": "https",
        "host": "api.example.com",
    }
    assert resolve(proxied, trust_proxy_headers=False) == {
        "scheme": "http",
        "host": "internal.svc",
        "port": 8080,
    }


def test_normalizes_the_scheme_to_lowercase_without_a_trailing_colon():
    # JS's location.protocol and Node's URL#protocol both yield "https:", and
    # nothing pins the case anywhere. intake never rewrites a stored row, so
    # the first release's spelling is permanent -- normalize on the way out.
    assert resolve(environ(HTTP_X_FORWARDED_PROTO="HTTPS"))["scheme"] == "https"
    assert resolve(environ(HTTP_X_FORWARDED_PROTO="https:"))["scheme"] == "https"


def test_keeps_an_ipv6_literal_whole_and_splits_its_port_off():
    assert resolve(environ(HTTP_HOST="[2001:DB8::1]:8443")) == {
        "scheme": "https",
        "host": "[2001:db8::1]",
        "port": 8443,
    }
