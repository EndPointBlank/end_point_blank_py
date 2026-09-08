"""
``GenerateAccessToken`` is the one call that mints credentials. Everything it
sends is derived from configuration, so a wrong key or a swallowed failure here
shows up much later as an unexplained 401.
"""

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.commands import generate_access_token as gat
from end_point_blank.commands.generate_access_token import GenerateAccessToken
from end_point_blank.configuration import Configuration
from end_point_blank.tokens.token_result import TokenOutcome, TokenResult


BASE = "https://api.example.com/orders"


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
            GenerateAccessToken.token(BASE)

        assert post.call_args[0][0] == "https://intake.test/api/access_token"

    def test_sends_the_base_url_the_token_is_for(self):
        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token(BASE)

        assert post.call_args[0][2] == {"base_url": BASE}

    def test_sends_the_base_url_verbatim(self):
        # Intake owns normalization and matches by longest path prefix. The SDK
        # altering the argument -- downcasing, trimming a trailing slash, or
        # reducing it to a hostname -- would change which environment the caller
        # asked for.
        messy = "https://API.Example.com:8443/Orders/"

        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token(messy)

        assert post.call_args[0][2]["base_url"] == messy

    def test_omits_the_ttl_when_none_is_configured(self):
        # Intake picks its own default TTL when the key is absent; sending an
        # explicit null would be a different request.
        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token(BASE)

        assert "token_ttl" not in post.call_args[0][2]

    def test_includes_the_ttl_when_one_is_configured(self, _config):
        _config.token_ttl = 900

        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token(BASE)

        assert post.call_args[0][2]["token_ttl"] == 900

    def test_authenticates_with_basic_credentials(self, _config):
        # The token endpoint is the bootstrap: it cannot be called with a Bearer
        # token, so it must always present client_id/client_secret.
        _config.client_id = "cid"
        _config.client_secret = "secret"

        with patch.object(gat, "post", return_value=response()) as post:
            GenerateAccessToken.token(BASE)

        assert post.call_args[0][1].startswith("Basic ")


class TestTheResult:
    def test_returns_the_parsed_body(self):
        # ``base_url`` comes back too: the canonical base URL intake resolved
        # the request to, which is what ``AccessTokens`` keys the entry on.
        body = {
            "token": "tok-abc",
            "expired_at": "2099-01-01T00:00:00Z",
            "base_url": "https://api.example.com/orders",
        }

        with patch.object(gat, "post", return_value=response(payload=body)):
            assert GenerateAccessToken.token(BASE) == body

    def test_returns_none_when_intake_is_unreachable(self):
        with patch.object(gat, "post", return_value=None):
            assert GenerateAccessToken.token(BASE) is None

    def test_returns_none_when_the_body_is_not_json(self):
        resp = response()
        resp.json.side_effect = ValueError("not json")

        with patch.object(gat, "post", return_value=resp):
            assert GenerateAccessToken.token(BASE) is None

    def test_returns_the_error_body_of_a_rejected_request(self):
        # The caller (``AccessTokens``) distinguishes "no token" from "no
        # response" and logs the reason, so a 4xx body has to come back rather
        # than being flattened to None.
        body = {"error": "invalid client"}

        with patch.object(gat, "post", return_value=response(401, payload=body)):
            assert GenerateAccessToken.token(BASE) == body


class TestTheStatusCarryingResult:
    """
    ``token_result`` is what a caller branches on. ``token`` cannot be: it hands
    back the parsed body whatever the status came back as, so a rejected
    credential and a broken server are literally the same value to it.

    The statuses are intake's, from ``access_token_controller``: 201 created,
    400 for a bad ``token_ttl`` or a missing ``base_url``, 401 for an invalid or
    revoked credential, 422 for "Missing target application" / "Missing source
    application" / "Failed to create access token", and 5xx for a genuine fault.
    """

    def test_a_2xx_is_a_success_carrying_the_parsed_body(self):
        body = {"token": "tok-abc", "expired_at": "2099-01-01T00:00:00Z", "base_url": BASE}

        with patch.object(gat, "post", return_value=response(201, payload=body)):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.SUCCESS
        assert result.status == 201
        assert result.payload == body

    def test_a_401_is_a_rejected_credential(self):
        # The whole point of the story: 401 means stop retrying and re-issue the
        # credential. Nothing the SDK does on its own will change the answer.
        body = {"error": "invalid client"}

        with patch.object(gat, "post", return_value=response(401, payload=body)):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.CREDENTIAL_REJECTED
        assert result.status == 401
        assert result.payload == body

    @pytest.mark.parametrize("status", [400, 403, 404, 422], ids=["bad-request", "forbidden", "not-found", "unprocessable"])
    def test_any_other_4xx_is_a_rejected_request(self, status):
        # Permanent, like a 401, but the remedy is not the credential: a 400 is
        # a malformed request and intake's 422 means the URL resolved to no
        # registered environment. Calling either a server error would tell the
        # caller to retry into a wall.
        with patch.object(gat, "post", return_value=response(status, payload={"error": "nope"})):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.REQUEST_REJECTED
        assert result.status == status

    @pytest.mark.parametrize("status", [500, 502, 503], ids=["error", "bad-gateway", "unavailable"])
    def test_a_5xx_is_a_server_error(self, status):
        with patch.object(gat, "post", return_value=response(status, payload={})):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.SERVER_ERROR
        assert result.status == status

    def test_no_response_at_all_is_a_transport_error(self):
        # ``post`` has already burned its three attempts by the time it answers
        # None, so this is "the network did not work", not "not tried yet".
        with patch.object(gat, "post", return_value=None):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.TRANSPORT_ERROR
        assert result.status is None
        assert result.payload is None

    def test_an_unparseable_success_body_is_a_transport_error(self):
        # A 201 is only useful for the token in it. Without a readable body
        # there is no token, and nothing distinguishes this from the response
        # never arriving.
        resp = response(201)
        resp.json.side_effect = ValueError("not json")

        with patch.object(gat, "post", return_value=resp):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.TRANSPORT_ERROR

    def test_an_unparseable_error_body_keeps_the_verdict_the_status_gave(self):
        # The status is the actionable part of a 401; the body is decoration. A
        # proxy that answers 401 with HTML is still a rejected credential, and
        # demoting it to a transport error would restart the retry loop this
        # story exists to stop.
        resp = response(401)
        resp.json.side_effect = ValueError("<html>")

        with patch.object(gat, "post", return_value=resp):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.CREDENTIAL_REJECTED
        assert result.status == 401
        assert result.payload is None

    def test_a_success_is_the_only_outcome_that_succeeded(self):
        results = {
            TokenOutcome.SUCCESS: True,
            TokenOutcome.CREDENTIAL_REJECTED: False,
            TokenOutcome.REQUEST_REJECTED: False,
            TokenOutcome.SERVER_ERROR: False,
            TokenOutcome.TRANSPORT_ERROR: False,
        }

        assert {o: TokenResult(o).succeeded for o in TokenOutcome} == results

    def test_only_a_server_or_transport_failure_is_retryable(self):
        # A caller's whole reason for asking. 401/400/422 are permanent until
        # something outside this process changes.
        results = {
            TokenOutcome.SUCCESS: False,
            TokenOutcome.CREDENTIAL_REJECTED: False,
            TokenOutcome.REQUEST_REJECTED: False,
            TokenOutcome.SERVER_ERROR: True,
            TokenOutcome.TRANSPORT_ERROR: True,
        }

        assert {o: TokenResult(o).retryable for o in TokenOutcome} == results

    def test_the_result_is_frozen(self):
        # Handed out by ``AccessTokens.last_failure`` and read from other
        # threads; a caller must not be able to edit the record in place.
        result = TokenResult(TokenOutcome.SERVER_ERROR, 500, None)

        with pytest.raises(dataclasses.FrozenInstanceError):
            result.outcome = TokenOutcome.SUCCESS


class TestTheLegacyContract:
    """``token`` is published API. It keeps returning parsed-body-or-None, for
    exactly the statuses it did before, so an existing caller sees no change."""

    @pytest.mark.parametrize("status", [201, 400, 401, 422, 500], ids=["created", "bad-request", "unauthorized", "unprocessable", "error"])
    def test_returns_the_parsed_body_whatever_the_status(self, status):
        body = {"error": "whatever"}

        with patch.object(gat, "post", return_value=response(status, payload=body)):
            assert GenerateAccessToken.token(BASE) == body

    def test_returns_exactly_the_payload_of_the_result(self):
        with patch.object(gat, "post", return_value=response(201, payload={"token": "t"})):
            assert GenerateAccessToken.token(BASE) == GenerateAccessToken.token_result(BASE).payload

    def test_still_logs_the_response_status(self, caplog):
        with caplog.at_level("INFO", logger=gat.__name__):
            with patch.object(gat, "post", return_value=response(201)):
                GenerateAccessToken.token(BASE)

        assert "Access token response: 201" in caplog.text
