"""
sc-1469: a client calling a provider must never send its own
client_id/client_secret to that provider. ``Authorization.header`` answers
Bearer or raises; only the SDK's own calls to intake present Basic.
"""
import base64
import pickle
import threading
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
import requests

import end_point_blank
from end_point_blank.authorization import Authorization
from end_point_blank.commands import _http
from end_point_blank.commands.basic_authenticate import BasicAuthenticate
from end_point_blank.commands.endpoint_update import EndpointUpdate
from end_point_blank.commands.generate_access_token import GenerateAccessToken
from end_point_blank.configuration import Configuration
from end_point_blank.token_unavailable_error import TokenUnavailableError
from end_point_blank.tokens.access_tokens import AccessTokens
from end_point_blank.tokens.token_result import TokenOutcome, TokenResult
from end_point_blank.writers.direct_writer import DirectWriter

INTAKE = "https://intake.test"
LOG_INTAKE = "https://log-intake.test"
PROVIDER = "https://api.example.com/orders"
PROVIDER_HOST = urlparse(PROVIDER).hostname
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
        self.bodies = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append((url, dict(headers or {})))
        self.bodies.append(json)
        if not is_provider(url):
            if isinstance(self._mint, Exception):
                raise self._mint
            return self._mint
        return response(200)

    def provider_requests(self):
        return [(url, h) for url, h in self.sent if is_provider(url)]


def is_provider(url):
    # Compare the parsed host, not a string prefix: "https://api.example.com.evil"
    # starts with PROVIDER's origin too.
    return urlparse(url).hostname == PROVIDER_HOST


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
        minted = TokenResult(TokenOutcome.SUCCESS, 201, {"token": "tok-abc", "base_url": PROVIDER})
        with patch.object(AccessTokens, "token_result", return_value=minted) as token_result:
            assert Authorization.header(PROVIDER) == "Bearer tok-abc"

        token_result.assert_called_once_with(PROVIDER)

    def test_a_cached_token_is_answered_without_a_second_mint(self, wire_with):
        wire = wire_with(response(201, {"token": "tok-abc", "base_url": PROVIDER,
                                        "expired_at": "2099-01-01T00:00:00Z"}))

        assert Authorization.header(PROVIDER) == "Bearer tok-abc"
        assert Authorization.header(PROVIDER + "/42") == "Bearer tok-abc"

        assert len(wire.sent) == 1


class TestNoTokenMeansNoCall:
    """Every way a mint can fail raises, and the provider never sees Basic --
    or anything at all, because there is nothing safe to send."""

    @pytest.mark.parametrize(
        "mint, outcome, reason",
        [
            (response(500, {"error": "boom"}), TokenOutcome.SERVER_ERROR, "intake failed to issue a token (HTTP 500)"),
            (response(422, {"error": "no environment"}), TokenOutcome.REQUEST_REJECTED, "intake refused the token request (HTTP 422)"),
            (response(401, {"error": "invalid"}), TokenOutcome.CREDENTIAL_REJECTED, "intake rejected this application's client credential (HTTP 401)"),
            (requests.Timeout("read timed out"), TokenOutcome.TRANSPORT_ERROR, "intake could not be reached"),
            (requests.ConnectionError("refused"), TokenOutcome.TRANSPORT_ERROR, "intake could not be reached"),
            (response(201, {"expired_at": "2099-01-01T00:00:00Z"}), TokenOutcome.SERVER_ERROR, "intake failed to issue a token (HTTP 201)"),
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
        # intake's body text and the exception's text are not the message's to repeat.
        for body_text in ("boom", "no environment", "invalid", "timed out", "refused;"):
            assert body_text not in message

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

    def test_the_error_carries_the_mint_status(self, wire_with):
        wire_with(response(422, {"error": "no environment"}))

        with pytest.raises(TokenUnavailableError) as raised:
            Authorization.header(PROVIDER)

        assert raised.value.status == 422

    def test_the_status_is_none_when_nothing_answered(self, wire_with):
        wire_with(requests.Timeout("read timed out"))

        with pytest.raises(TokenUnavailableError) as raised:
            Authorization.header(PROVIDER)

        assert raised.value.status is None

    def test_a_mint_that_raises_is_a_transport_error_with_the_cause(self, wire_with):
        wire = wire_with(response(201, {}))
        boom = RuntimeError("secret-bearing detail")

        with patch.object(GenerateAccessToken, "token_result", side_effect=boom):
            with pytest.raises(TokenUnavailableError) as raised:
                call_provider(wire)

        error = raised.value
        assert error.outcome is TokenOutcome.TRANSPORT_ERROR
        assert error.status is None
        assert error.cause is boom
        assert error.__cause__ is boom
        assert "the token request failed unexpectedly" in str(error)
        assert "secret-bearing detail" not in str(error)
        assert "RuntimeError" not in str(error)
        assert wire.sent == []


class TestUserinfoQueryAndFragmentAreStripped:
    """sc-1469 review: they can carry a secret, and intake's base-URL
    normalizer refuses any of them with a 422 -- so they are removed before the
    mint, and kept nowhere on the error."""

    RAW = PROVIDER.replace("https://", "https://user:hunter2@") + "?api_key=s3cret#frag"

    def test_the_mint_succeeds_and_intake_sees_only_the_stripped_url(self, wire_with):
        wire = wire_with(response(201, {"token": "tok-abc", "base_url": PROVIDER,
                                        "expired_at": "2099-01-01T00:00:00Z"}))

        assert Authorization.header(self.RAW) == "Bearer tok-abc"

        [(url, _)] = wire.sent
        assert url == f"{INTAKE}/api/access_token"
        assert wire.bodies == [{"base_url": PROVIDER}]

    def test_the_error_carries_only_the_stripped_url(self, wire_with):
        wire_with(response(422, {"error": "no environment"}))

        with pytest.raises(TokenUnavailableError) as raised:
            Authorization.header(self.RAW)

        assert raised.value.base_url == PROVIDER
        for secret in ("hunter2", "s3cret", "frag"):
            assert secret not in str(raised.value)
            assert secret not in repr(vars(raised.value))

    @pytest.mark.parametrize("url", ["not a url ?token=s3cret", "https:///orders", "https://h:x/"])
    def test_an_unparseable_url_makes_no_request(self, wire_with, url):
        wire = wire_with(response(201, {}))

        with pytest.raises(ValueError) as raised:
            Authorization.header(url)

        assert "s3cret" not in str(raised.value)
        assert wire.sent == []


REJECTED = TokenResult(TokenOutcome.CREDENTIAL_REJECTED, 401, {"error": "invalid"})
BROKEN = TokenResult(TokenOutcome.SERVER_ERROR, 500, {"error": "boom"})
PARENT = "https://api.example.com"


def minted(base_url):
    return TokenResult(TokenOutcome.SUCCESS, 201, {"token": "tok-other", "base_url": base_url,
                                                   "expired_at": "2099-01-01T00:00:00Z"})


class TestTheReasonBelongsToThisCall:
    """The failure record is shared per target and read outside the lock. The
    reason ``header`` raises with must be the one its own mint produced, not
    whatever another thread left in the record afterwards.

    Deterministic: the hook runs the other thread's mint to completion in the
    window between this call's mint (lock released) and its raise -- exactly
    where ``token()`` + ``last_failure()`` used to read the shared record."""

    @pytest.fixture
    def interleave(self):
        original = AccessTokens.token_result
        mints = []
        other = {}

        def fake_mint(url):
            return mints.pop(0)

        def hooked(self, url):
            result = original(self, url)
            if threading.current_thread() is threading.main_thread() and "call" in other:
                thread = threading.Thread(target=other.pop("call"))
                thread.start()
                thread.join()
            return result

        def install(this_mint, other_mint, other_call):
            mints.extend([this_mint, other_mint])
            other["call"] = other_call

        with patch.object(GenerateAccessToken, "token_result", side_effect=fake_mint), \
             patch.object(AccessTokens, "token_result", hooked):
            yield install

        assert mints == [], "the other thread's mint did not run"

    @pytest.mark.parametrize(
        "other_mint, other_url, left_behind",
        [
            (BROKEN, PROVIDER, BROKEN),
            (minted(PROVIDER), PROVIDER, None),
            (minted(PARENT), PARENT + "/invoices", None),
        ],
        ids=["another-thread-fails-differently", "another-thread-succeeds",
             "another-base-url-succeeds-and-clears-it"],
    )
    def test_a_concurrent_mint_cannot_change_this_calls_reason(
        self, interleave, other_mint, other_url, left_behind
    ):
        interleave(REJECTED, other_mint, lambda: AccessTokens().token(other_url))

        with pytest.raises(TokenUnavailableError) as raised:
            Authorization.header(PROVIDER)

        # The shared record now says something else (or nothing) ...
        assert AccessTokens().last_failure(PROVIDER) == left_behind
        # ... but this call reports its own 401.
        assert raised.value.failure == TokenResult(TokenOutcome.CREDENTIAL_REJECTED, 401)
        assert raised.value.outcome is TokenOutcome.CREDENTIAL_REJECTED
        assert raised.value.status == 401
        assert "intake rejected this application's client credential" in str(raised.value)

    def test_token_and_last_failure_keep_their_behaviour(self):
        with patch.object(GenerateAccessToken, "token_result", return_value=REJECTED):
            assert AccessTokens().token(PROVIDER) is None

        assert AccessTokens().last_failure(PROVIDER) == REJECTED


class TestNoUrlIsNoHeader:
    def test_the_no_argument_form_is_gone(self):
        with pytest.raises(TypeError):
            Authorization.header()

    @pytest.mark.parametrize("url", [None, ""])
    def test_an_empty_url_raises_without_a_token_lookup(self, url):
        with patch.object(AccessTokens, "token_result") as token_result:
            with pytest.raises(ValueError):
                Authorization.header(url)

        token_result.assert_not_called()


class TestTheError:
    def test_is_exported_from_the_package(self):
        assert end_point_blank.TokenUnavailableError is TokenUnavailableError
        assert "TokenUnavailableError" in end_point_blank.__all__

    def test_survives_pickling(self):
        failure = TokenResult(TokenOutcome.CREDENTIAL_REJECTED, 401, {"error": "invalid"})
        again = pickle.loads(pickle.dumps(TokenUnavailableError(PROVIDER, failure)))

        assert again.base_url == PROVIDER
        assert again.failure == TokenResult(TokenOutcome.CREDENTIAL_REJECTED, 401)
        assert again.status == 401
        assert str(again) == str(TokenUnavailableError(PROVIDER, failure))

    def test_a_pickled_cause_keeps_the_unexpected_failure_text(self):
        error = TokenUnavailableError(
            PROVIDER, TokenResult(TokenOutcome.TRANSPORT_ERROR), cause=RuntimeError("x")
        )
        again = pickle.loads(pickle.dumps(error))

        assert str(again) == str(error)
        assert isinstance(again.cause, RuntimeError)

    def test_the_failure_is_kept_without_its_payload(self):
        # A 2xx with a token but no base_url is a SERVER_ERROR whose payload
        # holds a live token; intake's error body is not the error's to keep.
        leaky = TokenResult(TokenOutcome.SERVER_ERROR, 201, {"token": "live-token"})

        error = TokenUnavailableError(PROVIDER, leaky)

        assert error.failure == TokenResult(TokenOutcome.SERVER_ERROR, 201)
        assert error.failure.payload is None
        assert "live-token" not in repr(vars(error))

    def test_a_hand_built_error_without_a_failure_still_renders(self):
        error = TokenUnavailableError(PROVIDER)

        assert error.failure is None
        assert error.outcome is None
        assert error.status is None
        assert str(error) == (
            f"Could not mint an EndPointBlank access token for {PROVIDER}: the token request "
            "failed for an unknown reason. "
            "EndPointBlank never sends this service's client_id/client_secret to a provider, so "
            "there is no Basic-auth fallback and the call must not be made without a token."
        )

    @pytest.mark.parametrize(
        "failure, cause, reason",
        [
            (REJECTED, None,
             "intake rejected this application's client credential (HTTP 401); retrying cannot "
             "help -- re-issue the credential"),
            (TokenResult(TokenOutcome.REQUEST_REJECTED, 422, {"error": "no environment"}), None,
             "intake refused the token request (HTTP 422); check the URL and that a grant "
             "covers the target"),
            (BROKEN, None, "intake failed to issue a token (HTTP 500); this may be transient"),
            (TokenResult(TokenOutcome.SERVER_ERROR), None,
             "intake failed to issue a token; this may be transient"),
            (TokenResult(TokenOutcome.TRANSPORT_ERROR), None,
             "intake could not be reached (timeout, connection refused or retries exhausted); "
             "this may be transient"),
            (TokenResult(TokenOutcome.TRANSPORT_ERROR), RuntimeError("boom"),
             "the token request failed unexpectedly"),
            (None, None, "the token request failed for an unknown reason"),
        ],
        ids=["credential_rejected", "request_rejected", "server_error",
             "server_error-without-status", "transport_error", "mint-raised", "no-result"],
    )
    def test_the_message_is_exactly_the_published_one(self, failure, cause, reason):
        error = TokenUnavailableError(PROVIDER, failure, cause=cause)

        assert str(error) == (
            f"Could not mint an EndPointBlank access token for {PROVIDER}: {reason}. "
            "EndPointBlank never sends this service's client_id/client_secret to a provider, "
            "so there is no Basic-auth fallback and the call must not be made without a token."
        )


class TestOwnIntakeCallsStillUseBasic:
    """intake already holds this service's credential; these must keep working."""

    def test_basic_credentials_encodes_correctly(self):
        decoded = base64.b64decode(Authorization.basic_credentials()).decode()
        assert decoded == "test-client-id:test-client-secret"

    def test_the_intake_header_is_basic_and_mints_nothing(self):
        with patch.object(AccessTokens, "token_result") as token_result:
            assert Authorization._intake_header() == BASIC

        token_result.assert_not_called()

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
