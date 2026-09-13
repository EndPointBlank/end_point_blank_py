import sys
from unittest.mock import patch

import flask
import pytest
from flask import Flask

from end_point_blank.middleware import report_interaction
from end_point_blank.middleware.report_interaction import ReportInteractionMiddleware
from end_point_blank.request_store import RequestStore
from end_point_blank.unauthorized_error import UnauthorizedError


def app_with_exception(propagate_exceptions):
    app = Flask(__name__)
    app.config["PROPAGATE_EXCEPTIONS"] = propagate_exceptions

    @app.get("/boom")
    def boom():
        raise RuntimeError("view failed")

    app.wsgi_app = ReportInteractionMiddleware(app.wsgi_app)
    return app


def app_with_exception_and_failing_error_handler():
    # A second, genuinely different exception in the same request: the view
    # raises RuntimeError, and the 500 handler that Flask invokes for it
    # raises a distinct TypeError of its own. TypeError is what actually
    # kills the request and reaches the client/WSGI server; RuntimeError
    # never escapes. Both are real exceptions that happened and both should
    # be reported — a same-exception dedup guard must not conflate the two.
    app = Flask(__name__)
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/boom")
    def boom():
        raise RuntimeError("view failed")

    @app.errorhandler(500)
    def handle_500(_error):
        raise TypeError("error handler itself failed")

    app.wsgi_app = ReportInteractionMiddleware(app.wsgi_app)
    return app


def test_reports_a_flask_exception_when_flask_converts_it_to_a_500():
    app = app_with_exception(False)

    with patch("end_point_blank.middleware.report_interaction.RequestWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ResponseWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ExceptionWriter.write") as write:
        response = app.test_client().get("/boom")

    assert response.status_code == 500
    write.assert_called_once()
    assert str(write.call_args.args[0]) == "view failed"


def test_reports_a_distinct_exception_raised_by_the_error_handler_itself():
    """The got_request_exception signal reports the view's RuntimeError
    while Flask is still resolving what to do with it; Flask then invokes
    the registered 500 handler, which itself raises a different exception
    (TypeError) that Flask does not catch — that TypeError is the one that
    actually escapes to the WSGI layer and the client.

    A dedup guard keyed on "something was reported this request" (rather
    than on which exception was reported) would see the flag already set
    from the RuntimeError and silently drop the TypeError — the exception
    that actually killed the request never gets reported. Both must be
    reported: they are two distinct failures.
    """
    app = app_with_exception_and_failing_error_handler()

    with patch("end_point_blank.middleware.report_interaction.RequestWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ResponseWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ExceptionWriter.write") as write:
        with pytest.raises(TypeError, match="error handler itself failed"):
            app.test_client().get("/boom")

    reported = [
        (type(call.args[0]).__name__, str(call.args[0])) for call in write.call_args_list
    ]
    assert ("TypeError", "error handler itself failed") in reported, (
        "the exception that actually escaped to the client was never reported"
    )
    assert ("RuntimeError", "view failed") in reported
    assert write.call_count == 2


def test_does_not_report_the_same_exception_twice_when_flask_propagates_it():
    app = app_with_exception(True)

    with patch("end_point_blank.middleware.report_interaction.RequestWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ResponseWriter.write"), \
         patch("end_point_blank.middleware.report_interaction.ExceptionWriter.write") as write:
        with pytest.raises(RuntimeError, match="view failed"):
            app.test_client().get("/boom")

    write.assert_called_once()
    assert str(write.call_args.args[0]) == "view failed"


def test_does_not_report_the_same_exception_object_delivered_twice_through_the_signal():
    """``got_request_exception`` is a single Blinker signal shared by every
    Flask app instance in the process, not scoped per-app or per-request.
    Flask's own ``handle_exception`` sends it at most once per request in the
    common case, but nothing stops the exact same exception object from
    reaching ``_report_flask_exception`` twice for what is really one
    failure — e.g. an app that composes multiple Flask/WSGI layers (a view
    that invokes another Flask app's ``wsgi_app`` internally and lets its
    exception propagate up to be re-handled by the outer app's own
    ``handle_exception``), or an extension that manually re-sends this
    signal to reuse this integration.

    This is the guard the prior fix-round docstring at
    ``_report_flask_exception`` describes: keyed on ``id(exception)`` so a
    second delivery of the *same* exception object is dropped, while a
    second, distinct exception (covered by
    ``test_reports_a_distinct_exception_raised_by_the_error_handler_itself``
    above) is still reported. Deleting that guard leaves this suite green
    everywhere else, because no other test ever delivers the same exception
    object through the signal twice — this is the missing regression test
    for it.
    """
    app = Flask(__name__)
    exc = RuntimeError("delivered twice, same object")

    RequestStore.set({})
    try:
        with patch(
            "end_point_blank.middleware.report_interaction.ExceptionWriter.write"
        ) as write:
            flask.got_request_exception.send(app, exception=exc)
            flask.got_request_exception.send(app, exception=exc)

        write.assert_called_once()
        assert write.call_args.args[0] is exc
    finally:
        RequestStore.clear()


def test_does_not_report_an_unauthorized_error_delivered_through_the_signal():
    """``UnauthorizedError`` represents an expected, intentional refusal (the
    caller's authenticate/authorize call was answered with a 401/403), not an
    application bug -- this module's own class docstring says as much:
    "re-raised without logging, as unauthorized access is expected behavior."

    A Flask app with default config (``PROPAGATE_EXCEPTIONS`` unset) swallows
    an unhandled view exception into a plain 500 and sends it through
    ``got_request_exception`` *before* the middleware's own
    ``except UnauthorizedError`` branch in ``__call__`` ever gets a chance to
    see it -- Flask already caught it. So the ``isinstance(exception,
    UnauthorizedError)`` half of the guard at
    ``report_interaction.py:28`` is the *only* thing standing between an
    expected refusal and it being filed as an application exception.

    Deleting ``or isinstance(exception, UnauthorizedError)`` from that guard
    leaves the rest of this suite green -- nothing else delivers an
    UnauthorizedError through this signal -- so this is the missing
    regression test for it.
    """
    app = Flask(__name__)
    exc = UnauthorizedError("authorize failed: forbidden", 403)

    RequestStore.set({})
    try:
        with patch(
            "end_point_blank.middleware.report_interaction.ExceptionWriter.write"
        ) as write:
            flask.got_request_exception.send(app, exception=exc)

        write.assert_not_called()
    finally:
        RequestStore.clear()


def test_does_not_raise_when_the_wrapping_app_never_set_up_the_middleware():
    """``got_request_exception.connect`` in ``_connect_flask_signal`` runs at
    *import* time and is process-global -- not scoped to any particular Flask
    app instance. That means the receiver fires for exceptions raised inside
    ANY Flask app in the process, including one that never wrapped its
    ``wsgi_app`` in :class:`ReportInteractionMiddleware` and therefore never
    called ``RequestStore.set`` -- ``RequestStore.get()`` is ``None`` for such
    an app's requests.

    Without the ``environ is None`` half of the guard at
    ``report_interaction.py:28``, the ``environ.get(...)`` call a few lines
    down raises ``AttributeError: 'NoneType' object has no attribute 'get'``
    from *inside* Flask's own ``handle_exception`` -- replacing the host
    app's clean 500 with an SDK crash of its own. That is exactly the
    scenario this half of the guard exists to prevent.

    Deleting that half of the guard leaves the rest of this suite green --
    nothing else sends this signal with ``RequestStore`` unset -- so this is
    the missing regression test for it.
    """
    app = Flask(__name__)
    exc = RuntimeError("view failed in an app that never wrapped itself")

    RequestStore.clear()  # simulate an app that never wrapped ReportInteractionMiddleware
    with patch(
        "end_point_blank.middleware.report_interaction.ExceptionWriter.write"
    ) as write:
        flask.got_request_exception.send(app, exception=exc)  # must not raise

    write.assert_not_called()


def test_connect_flask_signal_survives_flask_not_being_installed():
    """flask is not a base dependency of this package -- it is only pulled in,
    at ``>=2.3``, by the optional ``flask`` extra -- so a host can perfectly
    well install this SDK without flask at all. ``_connect_flask_signal`` runs
    unconditionally at import time (``report_interaction.py:75``), so
    ``from flask import got_request_exception`` failing with ``ImportError``
    must degrade to a no-op WSGI integration, exactly like the
    missing-blinker case covered by the test below -- not crash that host's
    import of this module.

    Nothing in the rest of this suite exercises a real ``ImportError`` here
    (flask is a hard dev-dependency of this test suite, so the only existing
    coverage of this ``except`` clause comes from the ``RuntimeError`` case
    below) -- dropping ``ImportError`` from the ``except (ImportError,
    RuntimeError)`` tuple leaves the rest of this suite green, so this is the
    missing regression test for it.
    """
    with patch.dict(sys.modules, {"flask": None}):
        report_interaction._connect_flask_signal()  # must not raise


def test_connect_flask_signal_survives_missing_blinker_signal_support():
    """On Flask < 2.3 without blinker installed, `from flask import
    got_request_exception` succeeds — the name exists as a no-op fake
    signal — but calling `.connect()` on it raises RuntimeError, not
    ImportError:
    "Signalling support is unavailable because the blinker library is not
    installed."

    flask is not a base dependency of this package (it's only pulled in,
    at >=2.3, by the optional "flask" extra), so nothing stops a host from
    pairing this SDK with an older Flask without blinker. Connecting the
    signal must degrade to a no-op WSGI integration rather than raise and
    crash that host's boot.
    """
    with patch.object(
        flask.got_request_exception,
        "connect",
        side_effect=RuntimeError(
            "Signalling support is unavailable because the blinker "
            "library is not installed."
        ),
    ):
        report_interaction._connect_flask_signal()  # must not raise
