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

from io import BytesIO
from unittest.mock import patch

import pytest

from end_point_blank.request_store import RequestStore

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
