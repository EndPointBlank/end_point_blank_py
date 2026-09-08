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

    def test_answers_none_for_a_rejected_request_without_losing_the_reason(self):
        # The comment this replaces claimed ``AccessTokens`` needed the 4xx body
        # back from here. It does not: it calls ``token_result`` and reads
        # ``result.payload`` directly, and nothing in ``src`` calls ``token`` at
        # all. So None costs no diagnostic -- the reason is still logged, and
        # still reachable.
        body = {"error": "invalid client"}

        with patch.object(gat, "post", return_value=response(401, payload=body)):
            assert GenerateAccessToken.token(BASE) is None

        with patch.object(gat, "post", return_value=response(401, payload=body)):
            assert GenerateAccessToken.token_result(BASE).payload == body


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

    def test_an_unparseable_success_body_is_a_server_error(self):
        # The one case where the body decides the outcome: a 201 is only useful
        # for the token in it, so a success the SDK cannot read is a broken
        # server. Still not a transport error -- a status did arrive.
        resp = response(201)
        resp.json.side_effect = ValueError("not json")

        with patch.object(gat, "post", return_value=resp):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.SERVER_ERROR
        assert result.status == 201
        assert result.payload is None

    def test_a_401_from_a_proxy_that_answers_html_is_still_a_rejected_credential(self):
        # The reason classification reads the status before the body. In prod
        # the SDK reaches intake through Caddy, and any proxy, WAF or auth
        # gateway in front of the app can answer 401 with an HTML page intake
        # never generated. Parsing first would call that transient and the
        # caller would retry a dead credential forever.
        resp = response(401)
        resp.json.side_effect = ValueError("Expecting value: line 1 column 1")

        with patch.object(gat, "post", return_value=resp):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.CREDENTIAL_REJECTED
        assert result.status == 401
        assert result.payload is None

    @pytest.mark.parametrize(
        "status,expected",
        [
            (400, TokenOutcome.REQUEST_REJECTED),
            (422, TokenOutcome.REQUEST_REJECTED),
            (503, TokenOutcome.SERVER_ERROR),
        ],
        ids=["bad-request", "unprocessable", "unavailable"],
    )
    def test_no_arrived_response_is_ever_a_transport_error(self, status, expected):
        # The invariant: TRANSPORT_ERROR means no usable HTTP status was
        # obtained. An unreadable body never demotes a status that arrived.
        resp = response(status)
        resp.json.side_effect = ValueError("<html>")

        with patch.object(gat, "post", return_value=resp):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is expected

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"token": "tok-abc"},
            {"base_url": BASE},
            {"token": "", "base_url": BASE},
            {"token": "tok-abc", "base_url": ""},
            {"error": "Missing target application"},
            {"token": 12345, "base_url": BASE},
            {"token": "tok-abc", "base_url": ["https://api.example.com/orders"]},
        ],
        ids=[
            "empty",
            "no-base-url",
            "no-token",
            "blank-token",
            "blank-base-url",
            "an-error-where-the-token-should-be",
            "non-string-token",
            "non-string-base-url",
        ],
    )
    def test_a_2xx_that_minted_nothing_usable_is_a_server_error(self, body):
        # SUCCESS has to mean a token AND somewhere to key it, or a caller
        # branching on the name alone caches nothing and never learns why.
        # SERVER_ERROR rather than a rejection: intake's base_url is NOT NULL
        # and it answers 4xx rather than minting when the URL resolves to
        # nothing, so a 2xx without one broke its own contract.
        #
        # Both fields have to be non-empty *strings*. A truthiness test would
        # pass a number or a list straight through to an ``Authorization``
        # header and to a cache key, where the same broken server costs a
        # confusing failure much further from the response that caused it.
        with patch.object(gat, "post", return_value=response(201, payload=body)):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.SERVER_ERROR
        # The real 2xx, not a fabricated 5xx: that is what actually happened.
        assert result.status == 201
        # Invariant: the body survives classification. It is what the failure
        # log reads to say *how* the response was useless, and what keeps
        # ``token`` answering exactly what it always did.
        assert result.payload == body

    @pytest.mark.parametrize(
        "body",
        [["token", "tok-abc"], "tok-abc", 7],
        ids=["array", "bare-string", "number"],
    )
    def test_a_2xx_whose_body_is_not_a_json_object_is_a_server_error(self, body):
        # Valid JSON that is not a document. The SDK runs on the request path of
        # the application it is embedded in, so this must classify rather than
        # raise ``AttributeError`` out of a customer's request because something
        # in front of intake answered 200 with an array.
        with patch.object(gat, "post", return_value=response(201, payload=body)):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.SERVER_ERROR
        assert result.status == 201
        assert result.payload == body

    def test_a_2xx_with_a_usable_mint_is_the_only_success(self):
        # The other side of the table above, stated once: SUCCESS is exactly a
        # 2xx whose body parsed and carries both non-empty strings, so a caller
        # that branches on it can read them without checking again.
        body = {"token": "tok-abc", "base_url": BASE, "expired_at": "2099-01-01T00:00:00Z"}

        with patch.object(gat, "post", return_value=response(200, payload=body)):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.SUCCESS
        assert result.payload["token"] == "tok-abc"
        assert result.payload["base_url"] == BASE

    def test_the_result_carries_no_retry_predicate(self):
        # Settled across all five SDKs: callers branch on the outcome names.
        # Two of the five are worth retrying and three are not, but the remedies
        # differ, and a boolean would collapse five honest names back into two.
        assert not hasattr(TokenResult(TokenOutcome.SERVER_ERROR), "retryable")
        assert not hasattr(TokenResult(TokenOutcome.SERVER_ERROR), "succeeded")

    def test_the_result_is_frozen(self):
        # Handed out by ``AccessTokens.last_failure`` and read from other
        # threads; a caller must not be able to edit the record in place.
        result = TokenResult(TokenOutcome.SERVER_ERROR, 500, None)

        with pytest.raises(dataclasses.FrozenInstanceError):
            result.outcome = TokenOutcome.SUCCESS


class TestTheLegacyContract:
    """``token`` is the payload-or-None accessor, and payload means a token was
    actually minted. Anything else answers None, matching all five SDKs."""

    @pytest.mark.parametrize("status", [201, 400, 401, 422, 500], ids=["created", "bad-request", "unauthorized", "unprocessable", "error"])
    def test_answers_none_for_a_body_that_minted_nothing(self, status):
        # Handing an ``{"error": ...}`` document back would give the caller a
        # truthy value for a request that produced no token -- the failure
        # ``token_result`` exists to remove, one layer down. The 201 in this
        # table is the interesting one: an encouraging status is not a mint.
        body = {"error": "whatever"}

        with patch.object(gat, "post", return_value=response(status, payload=body)):
            assert GenerateAccessToken.token(BASE) is None

    def test_the_body_is_still_reachable_through_the_result(self):
        # Nothing is lost by the None above. This is the same response read the
        # other way: the reason a caller might have wanted is on the result,
        # next to the outcome that explains it.
        body = {"error": "Missing target application", "detail": {"code": 422}}

        with patch.object(gat, "post", return_value=response(201, payload=body)):
            result = GenerateAccessToken.token_result(BASE)

        assert result.outcome is TokenOutcome.SERVER_ERROR
        assert result.status == 201
        assert result.payload == body

    def test_returns_the_payload_of_the_result_only_when_it_minted(self):
        minted = {"token": "t", "base_url": BASE}

        with patch.object(gat, "post", return_value=response(201, payload=minted)):
            assert GenerateAccessToken.token(BASE) == GenerateAccessToken.token_result(BASE).payload

        # ... and diverges from it otherwise: the result keeps the body, the
        # accessor reports that nothing was minted.
        with patch.object(gat, "post", return_value=response(201, payload={"token": "t"})):
            assert GenerateAccessToken.token_result(BASE).payload is not None
            assert GenerateAccessToken.token(BASE) is None

    def test_still_logs_the_response_status(self, caplog):
        with caplog.at_level("INFO", logger=gat.__name__):
            with patch.object(gat, "post", return_value=response(201)):
                GenerateAccessToken.token(BASE)

        assert "Access token response: 201" in caplog.text
