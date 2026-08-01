"""
The WSGI middleware's ``start_response`` wrapper is the only place the response
status and headers exist and have not yet been sent. Everything it does — status
capture, deprecation headers, forwarding the WSGI contract intact — has to happen
there or not at all.

Store and exception behaviour is covered in ``tests/test_middleware.py``.
"""

import sys
from io import BytesIO
from unittest.mock import patch

import pytest

from end_point_blank.middleware.report_interaction import ReportInteractionMiddleware
from end_point_blank.request_store import RequestStore


@pytest.fixture(autouse=True)
def _quiet_writers():
    RequestStore.clear()
    with patch("end_point_blank.middleware.report_interaction.RequestWriter"), \
         patch("end_point_blank.middleware.report_interaction.ResponseWriter") as response_writer, \
         patch("end_point_blank.middleware.report_interaction.ExceptionWriter"):
        yield response_writer
    RequestStore.clear()


def environ(**overrides):
    base = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/students",
        "QUERY_STRING": "",
        "wsgi.input": BytesIO(b""),
        "SERVER_NAME": "localhost",
        "HTTP_HOST": "localhost",
    }
    base.update(overrides)
    return base


def run(app, deprecation=None, start_response=None):
    """Runs one request through the middleware, capturing what the server saw."""
    seen = {}

    def default_start_response(status, headers, exc_info=None):
        seen["status"] = status
        seen["headers"] = headers
        seen["exc_info"] = exc_info

    def wrapped(env, start):
        # Stands in for @authorized, which stashes the block on the way in.
        if deprecation is not None:
            RequestStore.set_deprecation(deprecation)
        return app(env, start)

    middleware = ReportInteractionMiddleware(wrapped)
    list(middleware(environ(), start_response or default_start_response))
    return seen


class TestTheRecordedStatus:
    def test_records_the_status_code_from_the_status_line(self, _quiet_writers):
        run(lambda env, start: start("201 Created", []) or [b""])

        assert _quiet_writers.write.call_args.kwargs["status"] == 201

    def test_records_no_status_when_the_status_line_is_malformed(self, _quiet_writers):
        # A non-conforming WSGI app must not take the request down; intake
        # accepts a null status, an exception in the middleware does not.
        run(lambda env, start: start("not-a-status", []) or [b""])

        assert _quiet_writers.write.call_args.kwargs["status"] is None

    def test_records_no_status_when_the_app_never_responded(self, _quiet_writers):
        run(lambda env, start: [b""])

        assert _quiet_writers.write.call_args.kwargs["status"] is None


class TestTheWsgiContract:
    def test_the_status_line_reaches_the_server_unchanged(self):
        seen = run(lambda env, start: start("204 No Content", []) or [b""])

        assert seen["status"] == "204 No Content"

    def test_the_application_headers_reach_the_server(self):
        seen = run(lambda env, start: start("200 OK", [("X-Custom", "yes")]) or [b""])

        assert ("X-Custom", "yes") in seen["headers"]

    def test_exception_information_is_forwarded(self):
        # WSGI servers rely on exc_info to decide whether headers can still be
        # rewritten; swallowing it silently corrupts the error path.
        def app(env, start):
            try:
                raise ValueError("boom")
            except ValueError:
                start("500 Internal Server Error", [], sys.exc_info())
            return [b""]

        seen = run(app)

        assert seen["exc_info"][0] is ValueError

    def test_the_application_response_body_is_returned(self):
        middleware = ReportInteractionMiddleware(lambda env, start: start("200 OK", []) or [b"hello"])

        assert list(middleware(environ(), lambda s, h: None)) == [b"hello"]


class TestDeprecationHeaders:
    def test_adds_the_headers_when_the_version_is_deprecated(self):
        # These are added here because start_response is the last moment before
        # the headers are sent; the middleware's finally block is already too late.
        deprecation = {"deprecated_at": "2026-01-01T00:00:00Z", "sunset_at": "2026-12-31T23:59:59Z"}

        seen = run(lambda env, start: start("200 OK", []) or [b""], deprecation=deprecation)

        headers = dict(seen["headers"])
        assert headers["Deprecation"] == "@1767225600"
        assert headers["Sunset"] == "Thu, 31 Dec 2026 23:59:59 GMT"

    def test_adds_nothing_when_the_version_is_current(self):
        seen = run(lambda env, start: start("200 OK", []) or [b""])

        assert dict(seen["headers"]) == {}

    def test_the_added_headers_are_recorded_too(self, _quiet_writers):
        deprecation = {"deprecated_at": "2026-01-01T00:00:00Z"}

        run(lambda env, start: start("200 OK", []) or [b""], deprecation=deprecation)

        assert "Deprecation" in _quiet_writers.write.call_args.kwargs["headers"]

    def test_an_application_that_sets_its_own_header_keeps_it(self):
        # The application has said something more specific than we know.
        deprecation = {"sunset_at": "2026-12-31T23:59:59Z"}
        app_header = ("Sunset", "Wed, 01 Jan 2025 00:00:00 GMT")

        seen = run(lambda env, start: start("200 OK", [app_header]) or [b""], deprecation=deprecation)

        assert [v for name, v in seen["headers"] if name == "Sunset"] == [app_header[1]]
