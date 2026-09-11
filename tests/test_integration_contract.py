"""
The behaviours every framework integration must have, asserted against all of
them from one list.

This exists because of a real bug. The WSGI/Flask middleware emitted the RFC 9745
``Deprecation`` and RFC 8594 ``Sunset`` headers from the day the feature shipped;
the Django middleware never did. A Django provider served none of them, silently,
and it took running five real applications against a deprecated version to notice.

Neither coverage nor the per-integration test files could have caught it. The
Django package sat at 96-100% the whole time — coverage measures lines that
exist, and the missing code was *absent*, so there was no line to leave
uncovered. And each integration had its own test file with its own assertions,
so Django was free to have fewer behaviours than its sibling and still look
green.

The fix is structural: one list of behaviours, parametrized over every
integration. Adding an integration means adding an adapter below, and this list
becomes the checklist it has to satisfy. An integration that skips a behaviour
fails here rather than quietly having a shorter test file.

Behaviour specific to one framework still belongs in that framework's own file —
Django's request-body priming has no WSGI counterpart and does not belong here.
What belongs here is anything a *provider* would expect to be true no matter
which framework they happened to pick.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from unittest.mock import patch

import pytest

from end_point_blank.commands.authentication_cache import AuthenticationCache
from end_point_blank.configuration import Configuration
from end_point_blank.request_store import RequestStore
from end_point_blank.unauthorized_error import UnauthorizedError
from end_point_blank.writers.exception_writer import ExceptionWriter
from end_point_blank.writers.log_writer import LogWriter
from tests.intake_authorize import SOURCE_ENVIRONMENT_ID, granted

DEPRECATION = {"deprecated_at": "2026-01-01T00:00:00Z", "sunset_at": "2026-11-11T11:11:11Z"}
EXPECTED_DEPRECATION = "@1767225600"
EXPECTED_SUNSET = "Wed, 11 Nov 2026 11:11:11 GMT"


class Result:
    """What a provider's caller and intake respectively end up seeing."""

    def __init__(self, status, headers, recorded_headers):
        self.status = status
        self.headers = headers
        self.recorded_headers = recorded_headers

    def header(self, name):
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None


class WsgiIntegration:
    """The plain WSGI middleware, which Flask providers mount."""

    id = "wsgi"

    def respond(self, deprecation=None, app_headers=()):
        from end_point_blank.middleware.report_interaction import ReportInteractionMiddleware

        seen = {}

        def start_response(status, headers, exc_info=None):
            seen["status"] = int(status.split(" ", 1)[0])
            seen["headers"] = dict(headers)

        def app(_environ, start):
            if deprecation is not None:
                # Stands in for @authorized, which stashes the block on the way in.
                RequestStore.set_deprecation(deprecation)
            start("200 OK", [("Content-Type", "application/json"), *app_headers])
            return [b'{"ok":true}']

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/students",
            "QUERY_STRING": "",
            "wsgi.input": BytesIO(b""),
            "SERVER_NAME": "localhost",
            "HTTP_HOST": "localhost",
        }

        target = "end_point_blank.middleware.report_interaction"
        with patch(f"{target}.RequestWriter"), \
             patch(f"{target}.ResponseWriter") as response_writer, \
             patch(f"{target}.ExceptionWriter"):
            list(ReportInteractionMiddleware(app)(environ, start_response))
            recorded = response_writer.write.call_args.kwargs.get("headers", {})

        return Result(seen["status"], seen["headers"], dict(recorded))

    def refuse(self, error):
        """The status recorded for a refusal that propagates out of the app."""
        from end_point_blank.middleware.report_interaction import ReportInteractionMiddleware

        def app(_environ, _start):
            raise error

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/students",
            "QUERY_STRING": "",
            "wsgi.input": BytesIO(b""),
            "SERVER_NAME": "localhost",
            "HTTP_HOST": "localhost",
        }

        target = "end_point_blank.middleware.report_interaction"
        with patch(f"{target}.RequestWriter"), \
             patch(f"{target}.ResponseWriter") as response_writer, \
             patch(f"{target}.ExceptionWriter"):
            with pytest.raises(UnauthorizedError):
                list(ReportInteractionMiddleware(app)(environ, lambda s, h, e=None: None))

        return response_writer.write.call_args.kwargs["status"]

    def serve_authorized(self, view):
        """One request through the middleware and Flask's real ``@authorized``,
        running *view* inside the route. The writers are real; only where their
        rows go is not (see ``reported``)."""
        from flask import Flask

        from end_point_blank.flask import authorized
        from end_point_blank.middleware.report_interaction import ReportInteractionMiddleware

        app = Flask("provider")
        # So an unhandled error reaches the middleware, as it does under Django.
        # Flask otherwise renders it as a 500 inside its own wsgi_app.
        app.config["PROPAGATE_EXCEPTIONS"] = True

        @app.route("/students")
        @authorized
        def students():
            view()
            return {"ok": True}

        app.wsgi_app = ReportInteractionMiddleware(app.wsgi_app)
        app.test_client().get("/students", headers={"Authorization": "Basic Y2xpZW50"})


class DjangoIntegration:
    """The Django middleware."""

    id = "django"

    def respond(self, deprecation=None, app_headers=()):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from end_point_blank.django.middleware import ReportInteractionMiddleware

        response = HttpResponse('{"ok":true}', content_type="application/json")
        for name, value in app_headers:
            response[name] = value

        def get_response(_request):
            if deprecation is not None:
                RequestStore.set_deprecation(deprecation)
            return response

        target = "end_point_blank.django.middleware"
        with patch(f"{target}.RequestWriter"), \
             patch(f"{target}.ResponseWriter") as response_writer, \
             patch(f"{target}.ExceptionWriter"):
            result = ReportInteractionMiddleware(get_response)(RequestFactory().get("/students"))
            recorded = response_writer.write.call_args.kwargs.get("headers", {})

        return Result(result.status_code, dict(result.items()), dict(recorded))

    def refuse(self, error):
        """The status recorded for a refusal that propagates out of the view."""
        from django.test import RequestFactory

        from end_point_blank.django.middleware import ReportInteractionMiddleware

        def get_response(_request):
            raise error

        target = "end_point_blank.django.middleware"
        with patch(f"{target}.RequestWriter"), \
             patch(f"{target}.ResponseWriter") as response_writer, \
             patch(f"{target}.ExceptionWriter"):
            with pytest.raises(UnauthorizedError):
                ReportInteractionMiddleware(get_response)(RequestFactory().get("/students"))

        return response_writer.write.call_args.kwargs["status"]

    def serve_authorized(self, view):
        """One request through the middleware and Django's real ``@authorized``,
        running *view* inside the view function."""
        from django.http import HttpResponse
        from django.test import RequestFactory

        from end_point_blank.django import authorized
        from end_point_blank.django.middleware import ReportInteractionMiddleware

        @authorized
        def students(_request):
            view()
            return HttpResponse('{"ok":true}', content_type="application/json")

        request = RequestFactory().get("/students", HTTP_AUTHORIZATION="Basic Y2xpZW50")
        ReportInteractionMiddleware(students)(request)


INTEGRATIONS = [
    pytest.param(WsgiIntegration(), id=WsgiIntegration.id),
    pytest.param(DjangoIntegration(), id=DjangoIntegration.id),
]


@pytest.fixture(autouse=True)
def _clean_store():
    RequestStore.clear()
    yield
    RequestStore.clear()


@pytest.mark.parametrize("integration", INTEGRATIONS)
class TestDeprecationHeaderContract:
    def test_emits_both_headers_when_the_version_is_deprecated(self, integration):
        result = integration.respond(deprecation=DEPRECATION)

        assert result.header("Deprecation") == EXPECTED_DEPRECATION
        assert result.header("Sunset") == EXPECTED_SUNSET

    def test_emits_deprecation_alone_when_no_sunset_is_set(self, integration):
        # The normal starting state: going away, no deadline committed yet.
        result = integration.respond(
            deprecation={"deprecated_at": "2026-01-01T00:00:00Z", "sunset_at": None}
        )

        assert result.header("Deprecation") == EXPECTED_DEPRECATION
        assert result.header("Sunset") is None

    def test_emits_nothing_when_the_version_is_not_deprecated(self, integration):
        result = integration.respond(deprecation=None)

        assert result.header("Deprecation") is None
        assert result.header("Sunset") is None

    def test_never_overwrites_a_header_the_application_set(self, integration):
        # An application that sets its own Sunset has said something more
        # specific than we know.
        theirs = "Mon, 01 Jan 2029 00:00:00 GMT"

        result = integration.respond(
            deprecation=DEPRECATION, app_headers=[("Sunset", theirs)]
        )

        assert result.header("Sunset") == theirs
        assert result.header("Deprecation") == EXPECTED_DEPRECATION

    def test_records_the_headers_it_added(self, integration):
        # The audit row should show what the caller actually received. If the
        # headers go on after the response is recorded, intake's copy disagrees
        # with what went over the wire.
        result = integration.respond(deprecation=DEPRECATION)

        recorded = {k.lower() for k in result.recorded_headers}
        assert "deprecation" in recorded
        assert "sunset" in recorded

    def test_a_malformed_timestamp_does_not_break_the_response(self, integration):
        # A bad date is worth no header, not a 500 on a request that succeeded.
        result = integration.respond(deprecation={"deprecated_at": "not a date"})

        assert result.status == 200
        assert result.header("Deprecation") is None

    def test_the_application_response_reaches_the_caller_intact(self, integration):
        result = integration.respond(deprecation=DEPRECATION)

        assert result.status == 200
        assert result.header("Content-Type") == "application/json"


@pytest.mark.parametrize("integration", INTEGRATIONS)
class TestRequestStoreContract:
    def test_the_store_is_cleared_after_the_request(self, integration):
        # Servers reuse threads. A value left behind is readable by whoever is
        # served next on that thread.
        integration.respond(deprecation=DEPRECATION)

        assert RequestStore.get_deprecation() is None
        assert RequestStore.get_source_application_environment_id() is None


@pytest.mark.parametrize("integration", INTEGRATIONS)
class TestRefusalContract:
    """What intake is told about a request that was refused.

    Both integrations re-raise the refusal without reporting it as an
    application error -- that part was already shared. What was not shared was
    the response row: neither one recorded a status for it, because the refusal
    never reaches the response object either integration reads its status from.
    This is the behaviour a provider expects regardless of framework, so it is
    asserted against both from one place.
    """

    def test_the_refusing_status_reaches_the_response_row(self, integration):
        assert integration.refuse(UnauthorizedError("denied", 403)) == 403

    def test_a_rejected_credential_is_recorded_as_401(self, integration):
        assert integration.refuse(UnauthorizedError("nope", 401)) == 401

    def test_an_unreachable_check_is_recorded_as_503(self, integration):
        assert integration.refuse(UnauthorizedError("unavailable", 503)) == 503

    def test_a_refusal_is_never_recorded_without_a_status(self, integration):
        # Intake rejects a response row with a null status, so a refusal
        # recorded as None is a row that silently never lands at all.
        assert integration.refuse(UnauthorizedError("denied", 403)) is not None


class _Reported:
    """A stub intake on loopback, and the rows this SDK's writers produced."""

    def __init__(self):
        self.authorize_calls = []
        self.rows = {}

    def of(self, stream):
        return self.rows.get(stream, [])

    def callers_on(self, stream):
        return [row["source_application_environment_id"] for row in self.of(stream)]


@pytest.fixture
def reported(monkeypatch):
    """``@authorized`` makes its real HTTP call, to a stub intake on loopback
    that answers with intake's real 201 body; the four writers build their real
    payloads, which are collected per stream instead of sent."""
    for key in ("ENDPOINTBLANK_BASE_URL", "ENDPOINTBLANK_APP_NAME"):
        monkeypatch.delenv(key, raising=False)

    result = _Reported()

    class Intake(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's name
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            result.authorize_calls.append(self.path)
            body = json.dumps(granted()).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            """Silence the default stderr access log."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Intake)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class Collect:
        def __init__(self, stream):
            self.stream = stream

        def write(self, payloads):
            result.rows.setdefault(self.stream, []).extend(payloads)

    for stream in ("request", "response", "log", "exception"):
        monkeypatch.setattr(
            f"end_point_blank.writers.{stream}_writer._writer",
            lambda stream=stream: Collect(stream),
        )

    config = Configuration()
    config._init_defaults()
    config.base_url = f"http://127.0.0.1:{server.server_port}"
    config.app_name = "students-api"
    config.client_id = "provider-id"
    config.client_secret = "provider-secret"
    AuthenticationCache().clear()

    try:
        yield result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        AuthenticationCache().clear()
        config._init_defaults()


@pytest.mark.parametrize("integration", INTEGRATIONS)
class TestTheCallerIsNamedOnWhatIsReported:
    """Every response, log and error row for an authorized request names the
    calling service's application environment.

    Intake maps that id to the portal's environment, and the portal's error
    page renders it as the error's "Client". Recording it in the command is not
    the same as it reaching a row: the id has to be set where the writers read
    it, for the request they are writing about, in every integration. So this
    goes through the real middleware, the real decorator and the real writers,
    and asserts on the payloads (sc-473).
    """

    def test_the_response_log_and_error_rows_name_the_caller(self, integration, reported):
        def view():
            LogWriter.info("listed the students")
            ExceptionWriter.write(RuntimeError("handled here, and reported by hand"))

        integration.serve_authorized(view)

        assert reported.authorize_calls == ["/api/authorize"]
        assert reported.callers_on("response") == [SOURCE_ENVIRONMENT_ID]
        assert reported.callers_on("log") == [SOURCE_ENVIRONMENT_ID]
        assert reported.callers_on("exception") == [SOURCE_ENVIRONMENT_ID]

    def test_an_error_the_middleware_reports_names_the_caller(self, integration, reported):
        def view():
            raise RuntimeError("unhandled")

        with pytest.raises(RuntimeError, match="unhandled"):
            integration.serve_authorized(view)

        assert reported.callers_on("exception") == [SOURCE_ENVIRONMENT_ID]
        assert reported.callers_on("response") == [SOURCE_ENVIRONMENT_ID]

    def test_a_request_authorized_from_the_cache_names_the_caller_too(self, integration, reported):
        integration.serve_authorized(lambda: None)
        reported.rows.clear()

        integration.serve_authorized(lambda: LogWriter.info("listed the students again"))

        assert reported.authorize_calls == ["/api/authorize"], "the second request is a cache hit"
        assert reported.callers_on("response") == [SOURCE_ENVIRONMENT_ID]
        assert reported.callers_on("log") == [SOURCE_ENVIRONMENT_ID]

    def test_the_caller_does_not_outlive_the_request(self, integration, reported):
        # Servers reuse threads. A report written after the request -- a
        # background job on the same thread -- must not name its caller.
        integration.serve_authorized(lambda: None)

        LogWriter.info("after the request")

        assert reported.callers_on("log") == [None]
