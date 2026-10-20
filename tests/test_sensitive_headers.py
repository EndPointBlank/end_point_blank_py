"""``SENSITIVE_HEADERS`` names the headers no record ever carries (sc-1470)."""

from end_point_blank.sensitive_headers import (
    SENSITIVE_HEADERS,
    is_sensitive,
    without_sensitive_headers,
)


def test_names_the_credential_cookie_api_key_and_token_headers_lower_cased():
    assert SENSITIVE_HEADERS == {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-access-token",
        "x-refresh-token",
        "x-amz-security-token",
        "x-csrf-token",
        "x-xsrf-token",
        "x-goog-api-key",
        "ocp-apim-subscription-key",
    }


def test_drops_api_key_and_token_headers_in_any_letter_case():
    """sc-1626."""
    headers = {
        "X-Api-Key": "k1",
        "API-KEY": "k2",
        "x-auth-token": "t1",
        "X-Access-Token": "t2",
        "X-Amz-Security-Token": "t3",
        "Ocp-Apim-Subscription-Key": "k3",
        "X-Api-Version": "2",
    }

    assert without_sensitive_headers(headers) == {"X-Api-Version": "2"}


def test_cannot_be_changed_at_runtime():
    assert isinstance(SENSITIVE_HEADERS, frozenset)


def test_matches_in_any_letter_case():
    assert is_sensitive("Authorization")
    assert is_sensitive("PROXY-AUTHORIZATION")
    assert is_sensitive("Set-Cookie")
    assert not is_sensitive("X-Authorization-Hint")


def test_drops_the_listed_headers_and_keeps_the_rest():
    headers = {"Authorization": "Basic x", "cookie": "a=b", "Accept": "*/*"}

    assert without_sensitive_headers(headers) == {"Accept": "*/*"}
    assert headers == {"Authorization": "Basic x", "cookie": "a=b", "Accept": "*/*"}


def test_answers_an_empty_map_for_a_missing_one():
    assert without_sensitive_headers(None) == {}
    assert without_sensitive_headers({}) == {}
