from unittest.mock import patch

import flask
import pytest
from flask import Flask

from end_point_blank.middleware import report_interaction
from end_point_blank.middleware.report_interaction import ReportInteractionMiddleware
from end_point_blank.request_store import RequestStore


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
