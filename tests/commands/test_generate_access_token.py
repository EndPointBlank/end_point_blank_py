"""
``GenerateAccessToken`` is the one call that mints credentials. Everything it
sends is derived from configuration, so a wrong key or a swallowed failure here
shows up much later as an unexplained 401.
"""

from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.commands import generate_access_token as gat
from end_point_blank.commands.generate_access_token import GenerateAccessToken
from end_point_blank.configuration import Configuration


def response(status=201, payload=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {"token": "tok", "expired_at": "2099-01-01T00:00:00Z"}
    return resp


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    for key in ("ENDPOINTBLANK_BASE_URL", "ENDPOINTBLANK_CLIENT_ID", "ENDPOINTBLANK_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    config.base_url = "https://intake.test"
    yield config
    config._init_defaults()


class TestTheRequest:
    def test_posts_to_the_configured_access_token_url(self, _config):
        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token("api.example.com")

        assert post.call_args[0][0] == "https://intake.test/api/access_token"

    def test_sends_the_hostname_the_token_is_for(self):
        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token("api.example.com")

        assert post.call_args[0][2] == {"hostname": "api.example.com"}

    def test_omits_the_ttl_when_none_is_configured(self):
        # Intake picks its own default TTL when the key is absent; sending an
        # explicit null would be a different request.
        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token("api.example.com")

        assert "token_ttl" not in post.call_args[0][2]

    def test_includes_the_ttl_when_one_is_configured(self, _config):
        _config.token_ttl = 900

        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token("api.example.com")

        assert post.call_args[0][2]["token_ttl"] == 900

    def test_authenticates_with_basic_credentials(self, _config):
        # The token endpoint is the bootstrap: it cannot be called with a Bearer
        # token, so it must always present client_id/client_secret.
        _config.client_id = "cid"
        _config.client_secret = "secret"

        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token("api.example.com")

        assert post.call_args[0][1].startswith("Basic ")


class TestTheResult:
    def test_returns_the_parsed_body(self):
        body = {"token": "tok-abc", "expired_at": "2099-01-01T00:00:00Z"}

        with patch.object(gat, "post", return_value=response(payload=body)):
            assert GenerateAccessToken.token("api.example.com") == body

    def test_returns_none_when_intake_is_unreachable(self):
        with patch.object(gat, "post", return_value=None):
            assert GenerateAccessToken.token("api.example.com") is None

    def test_returns_none_when_the_body_is_not_json(self):
        resp = response()
        resp.json.side_effect = ValueError("not json")

        with patch.object(gat, "post", return_value=resp):
            assert GenerateAccessToken.token("api.example.com") is None

    def test_returns_the_error_body_of_a_rejected_request(self):
        # The caller (``AccessTokens``) distinguishes "no token" from "no
        # response" and logs the reason, so a 4xx body has to come back rather
        # than being flattened to None.
        body = {"error": "invalid client"}

        with patch.object(gat, "post", return_value=response(401, payload=body)):
            assert GenerateAccessToken.token("api.example.com") == body
