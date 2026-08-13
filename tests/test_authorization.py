import base64
from unittest.mock import patch

import pytest
from end_point_blank.configuration import Configuration
from end_point_blank.authorization import Authorization
from end_point_blank.tokens.access_tokens import AccessTokens


@pytest.fixture(autouse=True)
def configure():
    config = Configuration()
    config.client_id = "test-client-id"
    config.client_secret = "test-client-secret"
    yield
    config._init_defaults()


def test_basic_credentials_encodes_correctly():
    creds = Authorization.basic_credentials()
    decoded = base64.b64decode(creds).decode()
    assert decoded == "test-client-id:test-client-secret"


def test_header_with_no_base_url_returns_basic():
    # The no-argument form is what every call to intake itself uses -- intake
    # already holds this service's credential -- so it must never reach for a
    # token, which would recurse into the cache it feeds.
    header = Authorization.header()
    assert header.startswith("Basic ")
    encoded = header[len("Basic "):]
    decoded = base64.b64decode(encoded).decode()
    assert decoded == "test-client-id:test-client-secret"


def test_header_with_none_base_url_returns_basic():
    header = Authorization.header(base_url=None)
    assert header.startswith("Basic ")


class TestBearerTokens:
    """Presenting Basic credentials when a token was available is a wasted round
    trip; presenting a Bearer header when no token exists is a guaranteed 401."""

    def test_header_with_a_base_url_uses_the_cached_token(self):
        with patch.object(AccessTokens, "token", return_value="tok-abc"):
            assert Authorization.header("https://api.example.com/orders") == "Bearer tok-abc"

    def test_the_token_is_looked_up_for_the_requested_url(self):
        # Passed through untouched: the cache and intake both key on the URL as
        # given, so anything the SDK did to it here would change the answer.
        with patch.object(AccessTokens, "token", return_value="tok-abc") as token:
            Authorization.header("https://api.example.com/orders")

        token.assert_called_once_with("https://api.example.com/orders")

    def test_falls_back_to_basic_when_no_token_can_be_obtained(self):
        # Token generation failing must not stop the request from being
        # attempted — Basic credentials still authenticate.
        with patch.object(AccessTokens, "token", return_value=None):
            assert Authorization.header("https://api.example.com/orders").startswith("Basic ")

    def test_an_empty_base_url_does_not_attempt_a_token_lookup(self):
        with patch.object(AccessTokens, "token") as token:
            assert Authorization.header("").startswith("Basic ")

        token.assert_not_called()
