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


def test_does_not_crash_when_an_inner_middleware_instance_clears_the_store_first():
    """``ReportInteractionMiddleware`` is documented for wrapping a single WSGI
    app, but nothing stops a composed app -- one whose view calls a second,
    independently-wrapped WSGI/Flask app's ``wsgi_app`` and lets its exception
    propagate up through both layers. That is the same "composed apps"
    scenario the ``id(exception)`` dedup guard's own docstring, and the Flask
    signal's identity-dedup regression test in
    ``test_flask_exception_reporting.py``, were written to cover -- just one
    level further out, at the plain-WSGI ``__call__`` layer instead of the
    Flask signal.

    Both ``ReportInteractionMiddleware`` instances share the same
    ``RequestStore`` thread-local. The *inner* instance's own
    ``finally: RequestStore.clear()`` runs, on exception unwind, before the
    exception reaches the *outer* instance's ``except Exception`` handler --
    so by the time the outer instance calls
    ``_flask_exception_was_reported(exc)``, ``RequestStore.get()`` is already
    ``None``. Without the ``bool(environ)`` half of that function's guard
    (``bool(environ) and environ.get(...) == id(exception)``),
    ``environ.get(...)`` raises
    ``AttributeError: 'NoneType' object has no attribute 'get'`` from inside
    the outer middleware's own exception handler -- replacing the real
    ``RuntimeError`` (and silently losing its report) with an unrelated
    crash.

    Deleting ``bool(environ) and`` from ``_flask_exception_was_reported``
    leaves the rest of this suite green -- nothing else calls it with the
    store already cleared -- so this is the missing regression test for it.
    """

    def inner_app(environ, start_response):
        raise RuntimeError("boom from inner")

    inner = ReportInteractionMiddleware(inner_app)

    def outer_app(environ, start_response):
        list(inner(make_environ(), start_response))

    outer = ReportInteractionMiddleware(outer_app)

    with patch("end_point_blank.middleware.report_interaction.ExceptionWriter.write"):
        with pytest.raises(RuntimeError, match="boom from inner"):
            list(outer(make_environ(), lambda s, h: None))


def test_a_refusal_records_the_status_that_refused():
    # The WSGI middleware never sees start_response when a refusal propagates,
    # so the response row went to intake with a null status. The refusing status
    # lives on the exception.
    def app(environ, start_response):
        raise UnauthorizedError("Authorization failed: access_denied", 403)

    middleware = ReportInteractionMiddleware(app)

    with patch("end_point_blank.middleware.report_interaction.ResponseWriter") as response_writer:
        with pytest.raises(UnauthorizedError):
            list(middleware(make_environ(), lambda s, h: None))

    assert response_writer.write.call_args.kwargs["status"] == 403


def test_a_refusal_does_not_overwrite_a_status_the_application_already_sent():
    # If the app got as far as start_response, that is what the caller received
    # and it is the truer record.
    #
    # Guard, not a regression test: the pre-change code passes it too. It pins
    # the precedence the test above depends on.
    def app(environ, start_response):
        start_response("200 OK", [])
        raise UnauthorizedError("denied", 403)

    middleware = ReportInteractionMiddleware(app)

    with patch("end_point_blank.middleware.report_interaction.ResponseWriter") as response_writer:
        with pytest.raises(UnauthorizedError):
            list(middleware(make_environ(), lambda s, h: None))

    assert response_writer.write.call_args.kwargs["status"] == 200
