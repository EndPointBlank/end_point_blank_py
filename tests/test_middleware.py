import pytest
from unittest.mock import MagicMock, patch
from io import BytesIO

from end_point_blank.middleware.report_interaction import ReportInteractionMiddleware
from end_point_blank.request_store import RequestStore
from end_point_blank.unauthorized_error import UnauthorizedError


def make_environ(path="/api/v1/test", method="GET"):
    return {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "wsgi.input": BytesIO(b""),
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "localhost",
        "HTTP_HOST": "localhost",
    }


def test_stores_environ_in_request_store():
    captured = []

    def app(environ, start_response):
        captured.append(RequestStore.get())
        start_response("200 OK", [])
        return [b""]

    middleware = ReportInteractionMiddleware(app)
    environ = make_environ()
    list(middleware(environ, lambda s, h: None))

    assert captured[0] is environ


def test_clears_request_store_after_request():
    def app(environ, start_response):
        start_response("200 OK", [])
        return [b""]

    middleware = ReportInteractionMiddleware(app)
    list(middleware(make_environ(), lambda s, h: None))
    assert RequestStore.get() is None


def test_re_raises_exceptions():
    def app(environ, start_response):
        raise ValueError("Something broke")

    middleware = ReportInteractionMiddleware(app)

    with patch("end_point_blank.middleware.report_interaction.ExceptionWriter.write"):
        with pytest.raises(ValueError, match="Something broke"):
            list(middleware(make_environ(), lambda s, h: None))


def test_does_not_log_unauthorized_error():
    def app(environ, start_response):
        raise UnauthorizedError("Not allowed")

    middleware = ReportInteractionMiddleware(app)

    with patch("end_point_blank.middleware.report_interaction.ExceptionWriter.write") as mock_write:
        with pytest.raises(UnauthorizedError):
            list(middleware(make_environ(), lambda s, h: None))

        mock_write.assert_not_called()


def test_clears_store_even_after_exception():
    def app(environ, start_response):
        raise RuntimeError("crash")

    middleware = ReportInteractionMiddleware(app)

    with patch("end_point_blank.middleware.report_interaction.ExceptionWriter.write"):
        with pytest.raises(RuntimeError):
            list(middleware(make_environ(), lambda s, h: None))

    assert RequestStore.get() is None
