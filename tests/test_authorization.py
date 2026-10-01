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
from end_point_blank.configuration_error import ConfigurationError
from end_point_blank.strip_url import strip_url
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
        assert error.unexpected is True
        assert error.cause is boom
        assert error.__cause__ is boom
        assert "the token request failed unexpectedly" in str(error)
        assert "secret-bearing detail" not in str(error)
        assert "RuntimeError" not in str(error)
        assert wire.sent == []

    @pytest.mark.parametrize(
        "mint",
        [requests.exceptions.MissingSchema("no scheme"), requests.exceptions.InvalidURL("bad url"),
         requests.exceptions.InvalidHeader("bad header")],
        ids=["missing-schema", "invalid-url", "invalid-header"],
    )
    def test_a_request_error_that_is_not_transport_is_unexpected_and_not_retried(self, wire_with, mint):
        # sc-1469 review: only a request that never completed is a transport
        # error. A bad URL or header is a bug no retry can fix; reporting it as
        # "intake could not be reached" would send the reader to the network.
        wire = wire_with(mint)

        with pytest.raises(TokenUnavailableError) as raised:
            call_provider(wire)

        error = raised.value
        assert error.unexpected is True
        assert error.outcome is TokenOutcome.TRANSPORT_ERROR
        assert error.cause is mint
        assert "the token request failed unexpectedly" in str(error)
        assert len(wire.sent) == 1
        assert wire.provider_requests() == []

    def test_a_transport_error_is_not_unexpected(self, wire_with):
        wire_with(requests.ConnectionError("refused"))

        with pytest.raises(TokenUnavailableError) as raised:
            Authorization.header(PROVIDER)

        assert raised.value.unexpected is False
        assert raised.value.cause is None


class TestMissingCredentials:
    """sc-1469 review: a missing client_id or client_secret used to go out as
    Basic ``None:None``, come back 401, and be reported as a rejected credential
    to re-issue. It is a configuration error, raised before any request."""

    @pytest.fixture(autouse=True)
    def no_env(self, monkeypatch):
        monkeypatch.delenv("ENDPOINTBLANK_CLIENT_ID", raising=False)
        monkeypatch.delenv("ENDPOINTBLANK_CLIENT_SECRET", raising=False)

    @pytest.mark.parametrize(
        "client_id, client_secret, missing",
        [(None, "test-client-secret", "client_id"), ("test-client-id", "", "client_secret"),
         (None, None, "client_id and client_secret")],
        ids=["no-client-id", "empty-client-secret", "neither"],
    )
    def test_header_raises_it_as_itself_and_sends_nothing(
        self, configure, wire_with, client_id, client_secret, missing
    ):
        configure.client_id = client_id
        configure.client_secret = client_secret
        wire = wire_with(response(401, {"error": "invalid"}))

        with pytest.raises(ConfigurationError) as raised:
            Authorization.header(PROVIDER)

        assert f"EndPointBlank is missing {missing}:" in str(raised.value)
        assert wire.sent == []
        assert AccessTokens().last_failure(PROVIDER) is None

    def test_the_intake_header_raises_it(self, configure):
        configure.client_secret = None

        with pytest.raises(ConfigurationError):
            Authorization._intake_header()

    def test_the_mint_raises_it_rather_than_reporting_a_transport_error(self, configure, wire_with):
        configure.client_id = ""
        wire = wire_with(response(201, {}))

        with pytest.raises(ConfigurationError):
            GenerateAccessToken.token_result(PROVIDER)

        assert wire.sent == []

    def test_the_environment_supplies_them(self, configure, monkeypatch):
        configure.client_id = None
        configure.client_secret = None
        monkeypatch.setenv("ENDPOINTBLANK_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("ENDPOINTBLANK_CLIENT_SECRET", "test-client-secret")

        assert Authorization._intake_header() == BASIC

    def test_is_exported_from_the_package(self):
        assert end_point_blank.ConfigurationError is ConfigurationError
        assert "ConfigurationError" in end_point_blank.__all__


class TestBootRequestAndBackgroundPathsNeverRaise:
    """sc-1469 review on js#54, applied here: a missing credential now raises,
    and ``post`` lets a non-transport error through. Endpoint registration at
    boot, authenticate/authorize on a request, and the writers all used to
    answer or log a failure; they must still log it, never raise it into the
    host application. Authenticate/authorize treat it as an intake that did
    not answer, which the decorators refuse with 503."""

    @pytest.fixture
    def no_credentials(self, configure, monkeypatch):
        monkeypatch.delenv("ENDPOINTBLANK_CLIENT_ID", raising=False)
        monkeypatch.delenv("ENDPOINTBLANK_CLIENT_SECRET", raising=False)
        configure.client_id = None
        configure.client_secret = None

    ENVIRON = {"REQUEST_METHOD": "GET", "PATH_INFO": "/x", "HTTP_AUTHORIZATION": "Bearer caller"}

    def test_endpoint_registration_at_boot_logs_a_missing_credential(
        self, no_credentials, wire_with, caplog
    ):
        from flask import Flask
        from end_point_blank.flask import register_flask_endpoints

        wire = wire_with(response(201, {}))

        register_flask_endpoints(Flask(__name__))

        assert wire.sent == []
        assert "Endpoint update not sent to EndPointBlank: EndPointBlank is missing client_id" in caplog.text

    @pytest.mark.parametrize("call", ["authenticate", "authorize"])
    def test_authenticate_and_authorize_answer_none_for_a_missing_credential(
        self, no_credentials, wire_with, caplog, call
    ):
        from end_point_blank.commands.endpoint_authorize import EndpointAuthorize

        wire = wire_with(response(201, {}))

        if call == "authenticate":
            assert BasicAuthenticate.authenticate(dict(self.ENVIRON), "/x", None) is None
        else:
            assert EndpointAuthorize.authorize(dict(self.ENVIRON), "/x", None) is None

        assert wire.sent == []
        assert "EndPointBlank is missing client_id and client_secret" in caplog.text

    def test_the_authorized_decorator_refuses_with_503(self, no_credentials, wire_with):
        from flask import Flask
        from end_point_blank.flask.authorized import authorized
        from end_point_blank.unauthorized_error import UnauthorizedError

        wire_with(response(201, {}))
        app = Flask(__name__)

        @app.route("/x")
        @authorized
        def view():
            return "ok"

        with app.test_request_context("/x", headers={"Authorization": "Bearer caller"}):
            with pytest.raises(UnauthorizedError) as raised:
                view()

        assert raised.value.status_code == 503

    def test_a_request_error_that_is_not_transport_is_logged_by_class_only(self, wire_with, caplog):
        wire = wire_with(requests.exceptions.InvalidHeader("Authorization: " + BASIC))

        assert BasicAuthenticate.authenticate(dict(self.ENVIRON), "/x", None) is None
        EndpointUpdate([]).update()

        assert len(wire.sent) == 2  # One attempt each: nothing to retry.
        assert "failed unexpectedly (InvalidHeader)" in caplog.text
        assert BASIC.split()[1] not in caplog.text

    def test_the_writers_log_a_missing_credential(self, no_credentials, wire_with, caplog):
        from end_point_blank.request_store import RequestStore
        from end_point_blank.writers.exception_writer import ExceptionWriter
        from end_point_blank.writers.log_writer import LogWriter
        from end_point_blank.writers.request_writer import RequestWriter
        from end_point_blank.writers.response_writer import ResponseWriter

        wire = wire_with(response(201, {}))
        RequestStore.set(dict(self.ENVIRON))
        try:
            RequestWriter.write()
            ResponseWriter.write(200)
            LogWriter.write("hello", "info")
            ExceptionWriter.write(RuntimeError("boom"))
        finally:
            RequestStore.clear()

        assert wire.sent == []
        assert caplog.text.count("EndPointBlank is missing client_id") == 4


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

    @pytest.mark.parametrize(
        "given",
        [PROVIDER.replace(".com", ".com:443"), PROVIDER.replace(".com", ".com:"),
         PROVIDER.replace("https://api.example.com", "HTTPS://API.Example.COM:443")],
        ids=["default-port", "empty-port", "uppercase-scheme-and-host"],
    )
    def test_the_default_port_and_case_are_dropped_as_rails_drops_them(self, wire_with, given):
        # Parity with the Ruby gem's TargetUrl.strip (sc-1469 review): one
        # URL, one cache key and one form on the wire.
        wire = wire_with(response(201, {"token": "tok-abc", "base_url": PROVIDER,
                                        "expired_at": "2099-01-01T00:00:00Z"}))

        assert Authorization.header(given) == "Bearer tok-abc"

        assert wire.bodies == [{"base_url": PROVIDER}]

    @pytest.mark.parametrize(
        "url",
        ["not a url ?token=s3cret", "https:///orders", "https://h:x/", "https://h:0/x",
         "https://h:65536/x", "ftp://h:21/x?token=s3cret", "ws://h/x", "wss://h/x",
         "file://host/x", "mailto:s3cret@example.test", "foo://h/x"],
        ids=["no-scheme", "no-host", "non-numeric-port", "port-0", "port-65536", "ftp", "ws",
             "wss", "file", "mailto", "unknown-scheme"],
    )
    def test_an_unparseable_or_non_http_url_makes_no_request(self, wire_with, url):
        # Parity with rails#43: only http and https can name a provider, and a
        # port outside 1..65535 names nothing.
        wire = wire_with(response(201, {}))

        with pytest.raises(ValueError) as raised:
            Authorization.header(url)

        assert "s3cret" not in str(raised.value)
        assert "http or https" in str(raised.value)
        assert AccessTokens().token(url) is None
        assert wire.sent == []

    @pytest.mark.parametrize(
        "given, stripped",
        [("https://h:65535/x", "https://h:65535/x"), ("https://h:1/x", "https://h:1/x"),
         ("HTTP://H:8080/X", "http://h:8080/X")],
        ids=["port-65535", "port-1", "uppercase-http"],
    )
    def test_the_edges_of_what_is_accepted(self, given, stripped):
        assert strip_url(given) == stripped


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
            PROVIDER, TokenResult(TokenOutcome.TRANSPORT_ERROR), cause=RuntimeError("x"),
            unexpected=True,
        )
        again = pickle.loads(pickle.dumps(error))

        assert str(again) == str(error)
        assert again.unexpected is True
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
        "failure, unexpected, reason",
        [
            (REJECTED, False,
             "intake rejected this application's client credential (HTTP 401); retrying cannot "
             "help -- re-issue the credential"),
            (TokenResult(TokenOutcome.REQUEST_REJECTED, 422, {"error": "no environment"}), False,
             "intake refused the token request (HTTP 422); check the URL and that a grant "
             "covers the target"),
            (BROKEN, False, "intake failed to issue a token (HTTP 500); this may be transient"),
            (TokenResult(TokenOutcome.SERVER_ERROR), False,
             "intake failed to issue a token; this may be transient"),
            (TokenResult(TokenOutcome.TRANSPORT_ERROR), False,
             "intake could not be reached (timeout, connection refused or retries exhausted); "
             "this may be transient"),
            (TokenResult(TokenOutcome.TRANSPORT_ERROR), True,
             "the token request failed unexpectedly"),
            (None, False, "the token request failed for an unknown reason"),
        ],
        ids=["credential_rejected", "request_rejected", "server_error",
             "server_error-without-status", "transport_error", "mint-raised", "no-result"],
    )
    def test_the_message_is_exactly_the_published_one(self, failure, unexpected, reason):
        error = TokenUnavailableError(PROVIDER, failure, unexpected=unexpected)

        assert str(error) == (
            f"Could not mint an EndPointBlank access token for {PROVIDER}: {reason}. "
            "EndPointBlank never sends this service's client_id/client_secret to a provider, "
            "so there is no Basic-auth fallback and the call must not be made without a token."
        )


class TestOwnIntakeCallsStillUseBasic:
    """intake already holds this service's credential; these must keep working."""

    def test_basic_credentials_encodes_correctly_and_is_deprecated(self):
        with pytest.warns(DeprecationWarning, match="Authorization.header"):
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
