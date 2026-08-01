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


def test_header_with_no_hostname_returns_basic():
    header = Authorization.header()
    assert header.startswith("Basic ")
    encoded = header[len("Basic "):]
    decoded = base64.b64decode(encoded).decode()
    assert decoded == "test-client-id:test-client-secret"


def test_header_with_none_hostname_returns_basic():
    header = Authorization.header(hostname=None)
    assert header.startswith("Basic ")


class TestBearerTokens:
    """Every authorize call goes through here. Presenting Basic credentials when
    a token was available is a wasted round trip; presenting a Bearer header when
    no token exists is a guaranteed 401."""

    def test_header_with_a_hostname_uses_the_cached_token(self):
        with patch.object(AccessTokens, "token", return_value="tok-abc"):
            assert Authorization.header("api.example.com") == "Bearer tok-abc"

    def test_the_token_is_looked_up_for_the_requested_host(self):
        with patch.object(AccessTokens, "token", return_value="tok-abc") as token:
            Authorization.header("api.example.com")

        token.assert_called_once_with("api.example.com")

    def test_falls_back_to_basic_when_no_token_can_be_obtained(self):
        # Token generation failing must not stop the request from being
        # attempted — Basic credentials still authenticate.
        with patch.object(AccessTokens, "token", return_value=None):
            assert Authorization.header("api.example.com").startswith("Basic ")

    def test_an_empty_hostname_does_not_attempt_a_token_lookup(self):
        with patch.object(AccessTokens, "token") as token:
            assert Authorization.header("").startswith("Basic ")

        token.assert_not_called()
