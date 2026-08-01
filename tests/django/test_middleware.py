"""
The Django middleware is the integration with the most to go wrong: it sits in
the request path, drains the request body to record it, and has to hand that
body back to the view intact. The body-priming behaviour below is a fixed
production bug — POST handlers saw an empty body and rejected valid requests.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory

from end_point_blank.django.middleware import ReportInteractionMiddleware
from end_point_blank.request_store import RequestStore
from end_point_blank.unauthorized_error import UnauthorizedError


@pytest.fixture(autouse=True)
def _quiet_writers():
    RequestStore.clear()
    with patch("end_point_blank.django.middleware.RequestWriter") as request_writer, \
         patch("end_point_blank.django.middleware.ResponseWriter") as response_writer, \
         patch("end_point_blank.django.middleware.ExceptionWriter") as exception_writer:
        yield request_writer, response_writer, exception_writer
    RequestStore.clear()


@pytest.fixture
def request_writer(_quiet_writers):
    return _quiet_writers[0]


@pytest.fixture
def response_writer(_quiet_writers):
    return _quiet_writers[1]


@pytest.fixture
def exception_writer(_quiet_writers):
    return _quiet_writers[2]


def post_request(body='{"name":"ada"}'):
    return RequestFactory().post("/students", data=body, content_type="application/json")


class TestTheRequestStore:
    def test_the_environ_is_available_to_the_view(self):
        seen = []
        middleware = ReportInteractionMiddleware(lambda request: seen.append(RequestStore.get()) or HttpResponse("ok"))

        request = post_request()
        middleware(request)

        assert seen[0] is request.environ

    def test_the_environ_is_cleared_once_the_request_is_done(self):
        middleware = ReportInteractionMiddleware(lambda request: HttpResponse("ok"))

        middleware(post_request())

        assert RequestStore.get() is None

    def test_the_environ_is_cleared_even_when_the_view_raises(self):
        def view(request):
            raise RuntimeError("crash")

        middleware = ReportInteractionMiddleware(view)

        with pytest.raises(RuntimeError):
            middleware(post_request())

        assert RequestStore.get() is None


class TestTheRequestBody:
    def test_the_view_can_still_read_the_body_after_it_has_been_recorded(self):
        # Recording the request drains wsgi.input. Django's WSGIRequest holds its
        # own reference to that stream, so without priming, request.body inside
        # the view returns b"" and every POST handler sees an empty payload.
        seen = []
        middleware = ReportInteractionMiddleware(lambda request: seen.append(request.body) or HttpResponse("ok"))

        middleware(post_request('{"name":"ada"}'))

        assert seen[0] == b'{"name":"ada"}'

    def test_the_body_is_left_readable_for_the_recorder_too(self):
        middleware = ReportInteractionMiddleware(lambda request: HttpResponse("ok"))

        request = post_request('{"name":"ada"}')
        middleware(request)

        assert request.environ["wsgi.input"].getvalue() == b'{"name":"ada"}'

    def test_a_body_that_cannot_be_read_does_not_fail_the_request(self):
        request = post_request()
        type(request).body = property(lambda self: (_ for _ in ()).throw(OSError("stream gone")))
        try:
            middleware = ReportInteractionMiddleware(lambda r: HttpResponse("ok"))

            assert middleware(request).status_code == 200
        finally:
            del type(request).body


class TestWritingTheInteraction:
    def test_the_request_is_recorded_before_the_view_runs(self, request_writer):
        order = []
        request_writer.write.side_effect = lambda: order.append("request")
        middleware = ReportInteractionMiddleware(lambda r: order.append("view") or HttpResponse("ok"))

        middleware(post_request())

        assert order == ["request", "view"]

    def test_the_response_status_and_body_are_recorded(self, response_writer):
        middleware = ReportInteractionMiddleware(lambda r: HttpResponse("created", status=201))

        middleware(post_request())

        recorded = response_writer.write.call_args.kwargs
        assert recorded["status"] == 201
        assert recorded["body"] == "created"

    def test_the_response_headers_are_recorded(self, response_writer):
        def view(request):
            response = HttpResponse("ok")
            response["X-Custom"] = "yes"
            return response

        middleware = ReportInteractionMiddleware(view)
        middleware(post_request())

        assert response_writer.write.call_args.kwargs["headers"]["X-Custom"] == "yes"

    def test_a_streaming_response_is_recorded_without_consuming_it(self):
        # Reading .content off a streaming response raises; recording must not be
        # the thing that breaks a download endpoint.
        middleware = ReportInteractionMiddleware(lambda r: StreamingHttpResponse(iter([b"a", b"b"])))

        response = middleware(post_request())

        assert b"".join(response.streaming_content) == b"ab"

    def test_a_response_whose_headers_cannot_be_read_still_records(self, response_writer):
        response = MagicMock()
        response.status_code = 200
        response.items.side_effect = TypeError("not a header mapping")
        response.content = b"ok"
        middleware = ReportInteractionMiddleware(lambda r: response)

        middleware(post_request())

        assert response_writer.write.call_args.kwargs["headers"] == {}


class TestWhenTheViewRaises:
    def test_the_exception_reaches_django(self, exception_writer):
        def view(request):
            raise RuntimeError("crash")

        middleware = ReportInteractionMiddleware(view)

        with pytest.raises(RuntimeError, match="crash"):
            middleware(post_request())

    def test_the_exception_is_reported(self, exception_writer):
        error = RuntimeError("crash")
        middleware = ReportInteractionMiddleware(lambda r: (_ for _ in ()).throw(error))

        with pytest.raises(RuntimeError):
            middleware(post_request())

        exception_writer.write.assert_called_once_with(error)

    def test_a_status_already_known_is_not_overwritten_by_the_synthetic_one(self, response_writer):
        # The 500 is a last resort for when the response never formed. If the app
        # answered 201 and the failure came afterwards, recording 500 would
        # misreport a request that actually succeeded.
        response = MagicMock()
        response.status_code = 201
        response.items.return_value = []
        type(response).content = property(lambda self: (_ for _ in ()).throw(TypeError("unreadable")))
        middleware = ReportInteractionMiddleware(lambda r: response)

        with pytest.raises(TypeError):
            middleware(post_request())

        assert response_writer.write.call_args.kwargs["status"] == 201

    def test_a_response_row_is_still_recorded(self, response_writer):
        # An outer middleware renders the error, so we never see the real status.
        # Intake rejects a response row with no status, so one is synthesised.
        middleware = ReportInteractionMiddleware(lambda r: (_ for _ in ()).throw(RuntimeError("crash")))

        with pytest.raises(RuntimeError):
            middleware(post_request())

        recorded = response_writer.write.call_args.kwargs
        assert recorded["status"] == 500
        assert recorded["body"] == "RuntimeError: crash"


class TestUnauthorizedRequests:
    def test_the_error_reaches_django(self):
        middleware = ReportInteractionMiddleware(lambda r: (_ for _ in ()).throw(UnauthorizedError("nope")))

        with pytest.raises(UnauthorizedError):
            middleware(post_request())

    def test_it_is_not_reported_as_an_application_error(self, exception_writer):
        # A rejected caller is the system working. Reporting it would bury real
        # errors under routine denials.
        middleware = ReportInteractionMiddleware(lambda r: (_ for _ in ()).throw(UnauthorizedError("nope")))

        with pytest.raises(UnauthorizedError):
            middleware(post_request())

        exception_writer.write.assert_not_called()


class TestProcessException:
    def test_reports_an_application_error(self, exception_writer):
        # Django runs process_exception before the exception can reach __call__,
        # so without this hook an error rendered by an outer middleware would
        # never be reported at all.
        middleware = ReportInteractionMiddleware(lambda r: HttpResponse("ok"))
        error = ValueError("boom")

        assert middleware.process_exception(post_request(), error) is None
        exception_writer.write.assert_called_once_with(error)

    def test_ignores_an_unauthorized_error(self, exception_writer):
        middleware = ReportInteractionMiddleware(lambda r: HttpResponse("ok"))

        assert middleware.process_exception(post_request(), UnauthorizedError("nope")) is None
        exception_writer.write.assert_not_called()
