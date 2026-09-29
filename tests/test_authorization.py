"""
sc-1469: a client calling a provider must never send its own
client_id/client_secret to that provider. ``Authorization.header`` answers
Bearer or raises; only the SDK's own calls to intake present Basic.
"""
import base64
import pickle
from unittest.mock import MagicMock, patch

import pytest
import requests

import end_point_blank
from end_point_blank.authorization import Authorization
from end_point_blank.commands import _http
from end_point_blank.commands.basic_authenticate import BasicAuthenticate
from end_point_blank.commands.endpoint_update import EndpointUpdate
from end_point_blank.configuration import Configuration
from end_point_blank.token_unavailable_error import TokenUnavailableError
from end_point_blank.tokens.access_tokens import AccessTokens
from end_point_blank.tokens.token_result import TokenOutcome, TokenResult
from end_point_blank.writers.direct_writer import DirectWriter

INTAKE = "https://intake.test"
LOG_INTAKE = "https://log-intake.test"
PROVIDER = "https://api.example.com/orders"
BASIC = "Basic " + base64.b64encode(b"test-client-id:test-client-secret").decode()


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    for key in ("ENDPOINTBLANK_BASE_URL", "ENDPOINTBLANK_CLIENT_ID", "ENDPOINTBLANK_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    config.base_url = INTAKE
    config.log_base_url = LOG_INTAKE
    config.client_id = "test-client-id"
    config.client_secret = "test-client-secret"
    AccessTokens().clear()
    yield config
    AccessTokens().clear()
    config._init_defaults()


def response(status, payload=None):
    resp = MagicMock()
    resp.status_code = status
    resp.text = ""
    resp.json.return_value = payload if payload is not None else {}
    return resp


class Wire:
    """Stands in for the HTTP session under ``_http.post`` and records every
    request the SDK -- or the application calling a provider -- puts on it."""

    def __init__(self, mint):
        self._mint = mint
        self.sent = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append((url, dict(headers or {})))
        if not url.startswith(PROVIDER):
            if isinstance(self._mint, Exception):
                raise self._mint
            return self._mint
        return response(200)

    def provider_requests(self):
        return [(url, h) for url, h in self.sent if url.startswith(PROVIDER)]


@pytest.fixture
def wire_with():
    patches = []

    def install(mint):
        wire = Wire(mint)
        p = patch.object(_http, "_session", return_value=wire)
        p.start()
        patches.append(p)
        s = patch.object(_http.time, "sleep")
        s.start()
        patches.append(s)
        return wire

    yield install
    for p in patches:
        p.stop()


def call_provider(wire, url=PROVIDER):
    """What an application does: build the header, then make the call."""
    wire.post(url, headers={"Authorization": Authorization.header(url)})


class TestProviderCallsGetBearer:
    def test_a_minted_token_is_sent_as_bearer(self, wire_with):
        wire = wire_with(response(201, {"token": "tok-abc", "base_url": PROVIDER,
                                        "expired_at": "2099-01-01T00:00:00Z"}))

        call_provider(wire)

        [(url, headers)] = wire.provider_requests()
        assert url == PROVIDER
        assert headers["Authorization"] == "Bearer tok-abc"

    def test_the_token_is_looked_up_for_the_requested_url(self):
        with patch.object(AccessTokens, "token", return_value="tok-abc") as token:
            assert Authorization.header(PROVIDER) == "Bearer tok-abc"

        token.assert_called_once_with(PROVIDER)


class TestNoTokenMeansNoCall:
    """Every way a mint can fail raises, and the provider never sees Basic --
    or anything at all, because there is nothing safe to send."""

    @pytest.mark.parametrize(
        "mint, outcome, reason",
        [
            (response(500, {"error": "boom"}), TokenOutcome.SERVER_ERROR, "HTTP 500: boom"),
            (response(422, {"error": "no environment"}), TokenOutcome.REQUEST_REJECTED, "HTTP 422"),
            (response(401, {"error": "invalid"}), TokenOutcome.CREDENTIAL_REJECTED, "rejected this service's credential"),
            (requests.Timeout("read timed out"), TokenOutcome.TRANSPORT_ERROR, "could not be reached"),
            (requests.ConnectionError("refused"), TokenOutcome.TRANSPORT_ERROR, "could not be reached"),
            (response(201, {"expired_at": "2099-01-01T00:00:00Z"}), TokenOutcome.SERVER_ERROR, "no token in response"),
        ],
        ids=["5xx", "4xx", "401", "timeout", "connection-refused", "2xx-without-token"],
    )
    def test_raises_and_sends_nothing_to_the_provider(self, wire_with, mint, outcome, reason):
        wire = wire_with(mint)

        with pytest.raises(TokenUnavailableError) as raised:
            call_provider(wire)

        error = raised.value
        assert error.base_url == PROVIDER
        assert error.outcome is outcome
        message = str(error)
        assert message.startswith(f"Could not mint an EndPointBlank access token for {PROVIDER}: ")
        assert reason in message
        assert "never sends this service's client_id/client_secret to a provider" in message

        assert wire.provider_requests() == []
        for url, headers in wire.sent:
            # The only Basic on the wire is the mint itself, to intake.
            assert url == f"{INTAKE}/api/access_token"
            assert headers["Authorization"] == BASIC

    def test_the_credential_is_not_in_the_message(self, wire_with):
        wire_with(response(401, {"error": "invalid"}))

        with pytest.raises(TokenUnavailableError) as raised:
            Authorization.header(PROVIDER)

        assert "test-client-secret" not in str(raised.value)
        assert BASIC.split()[1] not in str(raised.value)

    def test_a_missing_failure_record_still_raises(self):
        # Another thread's successful mint can clear the record in between.
        with patch.object(AccessTokens, "token", return_value=None), \
             patch.object(AccessTokens, "last_failure", return_value=None):
            with pytest.raises(TokenUnavailableError, match="no reason was recorded") as raised:
                Authorization.header(PROVIDER)

        assert raised.value.failure is None
        assert raised.value.outcome is None


class TestNoUrlIsNoHeader:
    def test_the_no_argument_form_is_gone(self):
        with pytest.raises(TypeError):
            Authorization.header()

    @pytest.mark.parametrize("url", [None, ""])
    def test_an_empty_url_raises_without_a_token_lookup(self, url):
        with patch.object(AccessTokens, "token") as token:
            with pytest.raises(ValueError):
                Authorization.header(url)

        token.assert_not_called()


class TestTheError:
    def test_is_exported_from_the_package(self):
        assert end_point_blank.TokenUnavailableError is TokenUnavailableError
        assert "TokenUnavailableError" in end_point_blank.__all__

    def test_survives_pickling(self):
        failure = TokenResult(TokenOutcome.CREDENTIAL_REJECTED, 401, {"error": "invalid"})
        again = pickle.loads(pickle.dumps(TokenUnavailableError(PROVIDER, failure)))

        assert again.base_url == PROVIDER
        assert again.failure == failure
        assert str(again) == str(TokenUnavailableError(PROVIDER, failure))


class TestOwnIntakeCallsStillUseBasic:
    """intake already holds this service's credential; these must keep working."""

    def test_basic_credentials_encodes_correctly(self):
        decoded = base64.b64decode(Authorization.basic_credentials()).decode()
        assert decoded == "test-client-id:test-client-secret"

    def test_the_intake_header_is_basic_and_mints_nothing(self):
        with patch.object(AccessTokens, "token") as token:
            assert Authorization._intake_header() == BASIC

        token.assert_not_called()

    def test_the_writers_the_authenticate_and_the_endpoint_update_present_basic(self, wire_with):
        wire = wire_with(response(201, {}))
        environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/x", "HTTP_AUTHORIZATION": "Bearer caller"}

        DirectWriter("log_url").write([{"a": 1}])
        BasicAuthenticate.authenticate(environ, "/x", None)
        EndpointUpdate([]).update()

        assert [url for url, _ in wire.sent] == [
            f"{LOG_INTAKE}/api/application_logs",
            Configuration().authorize_url,
            Configuration().endpoint_update_url,
        ]
        for url, headers in wire.sent:
            assert headers["Authorization"] == BASIC
