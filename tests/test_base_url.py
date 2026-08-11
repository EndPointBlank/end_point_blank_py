"""
``base_url`` resolves what the caller used, not what the process sees.

This module reads the WSGI environ directly rather than going through any
framework helper. WSGI has no notion of a trusted proxy at all, and the four
sibling clients each had a different one -- which is exactly why the same
request produced five different hosts.
"""

from end_point_blank.base_url import hostname, resolve


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


# --- Fix round 1: default-port synthesis, and malformed forwarded headers ---


def test_omits_the_scheme_and_port_when_only_host_and_port_are_forwarded():
    # X-Forwarded-Host and X-Forwarded-Port both validate, but no
    # X-Forwarded-Proto was sent. Without a resolved scheme there is no
    # default port to compare 443 against, so the port is left out entirely
    # rather than reported unconditionally -- one origin must not get two
    # different (scheme, host, port) groupings depending on whether a proxy
    # happened to send one extra header.
    resolved = resolve(
        environ(HTTP_X_FORWARDED_HOST="api.example.com", HTTP_X_FORWARDED_PORT="443")
    )

    assert resolved == {"host": "api.example.com"}


def test_ignores_a_malformed_forwarded_port_rather_than_erasing_the_authority_port():
    # X-Forwarded-Port is garbage. It must not count as proxy evidence -- the
    # connection's own scheme stays trustworthy -- and it must not swallow the
    # fallback to the port embedded in the Host header either.
    resolved = resolve(
        environ(
            **{
                "wsgi.url_scheme": "https",
                "SERVER_PORT": "8080",
                "HTTP_HOST": "api.example.com:9000",
                "HTTP_X_FORWARDED_PORT": "not-a-port",
            }
        )
    )

    assert resolved == {"scheme": "https", "host": "api.example.com", "port": 9000}


def test_ignores_a_malformed_forwarded_proto_rather_than_erasing_the_scheme():
    # X-Forwarded-Proto is garbage; X-Forwarded-Host validates. Host presence
    # is not proxy evidence on its own -- it was already caller-controlled via
    # the Host header regardless of trust_proxy_headers -- so the junk proto
    # must not erase the connection's scheme either.
    resolved = resolve(
        environ(
            SERVER_PORT="443",
            HTTP_X_FORWARDED_PROTO="not a scheme",
            HTTP_X_FORWARDED_HOST="api.example.com",
        )
    )

    assert resolved == {"scheme": "https", "host": "api.example.com"}


def test_drops_a_forwarded_host_longer_than_dns_allows():
    # DNS caps a hostname at 253 bytes; nothing longer can be real. It is
    # dropped, not truncated -- a truncated hostname is a plausible-looking
    # wrong value, and the portal reads this field verbatim. The oversized
    # header doesn't validate, so it falls back like an absent one; with no
    # Host/SERVER_NAME to fall back to here, host resolves to nothing, while
    # scheme and port still resolve normally from their own sources.
    resolved = resolve(
        {
            "wsgi.url_scheme": "https",
            "SERVER_PORT": "8443",
            "HTTP_X_FORWARDED_HOST": "a" * 300,
        }
    )

    assert "host" not in resolved
    assert resolved == {"scheme": "https", "port": 8443}


def test_keeps_a_host_at_the_dns_length_cap_and_drops_one_byte_over():
    at_cap = "a" * 253
    over_cap = "a" * 254

    assert resolve(environ(HTTP_HOST=at_cap))["host"] == at_cap
    assert "host" not in resolve(environ(HTTP_HOST=over_cap))


def test_hostname_lowercases_the_host_and_strips_the_port():
    assert hostname(environ()) == "api.example.com"


def test_hostname_keeps_an_ipv6_literal_whole_and_bracketed():
    # '[::1]:8443'.split(':')[0] is '[' -- the bug this replaces.
    assert hostname(environ(HTTP_HOST="[2001:DB8::1]:8443")) == "[2001:db8::1]"


def test_hostname_ignores_forwarded_host_even_though_resolve_honors_it():
    # target_hostname is the portal's application-environment lookup key. A
    # value matching no registered row is a hard 422, not a cache miss.
    proxied = environ(HTTP_HOST="internal.svc", HTTP_X_FORWARDED_HOST="api.example.com")

    assert hostname(proxied) == "internal.svc"
    assert resolve(proxied)["host"] == "api.example.com"


def test_hostname_falls_back_to_server_name_without_a_host_header():
    assert hostname(environ(HTTP_HOST=None)) == "api.example.com"


def test_hostname_is_none_for_a_host_that_is_not_shaped_like_a_hostname():
    assert hostname(environ(HTTP_HOST="api.example.com/../evil")) is None


def test_hostname_is_none_for_a_host_longer_than_dns_allows():
    assert hostname(environ(HTTP_HOST="a" * 250 + ".example.com", SERVER_NAME=None)) is None


def test_hostname_is_none_when_handed_something_that_is_not_an_environ():
    assert hostname(None) is None
