"""
``@authenticated`` is the Flask counterpart to ``@authorized``: it validates the
caller's credentials rather than their access to a specific endpoint version, and
unlike authorization it is never cached.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from end_point_blank.flask.authenticated import authenticated
from end_point_blank.unauthorized_error import UnauthorizedError

AUTHENTICATE = "end_point_blank.commands.basic_authenticate.BasicAuthenticate.authenticate"


@pytest.fixture
def app():
    application = Flask(__name__)

    @application.route("/students/<int:student_id>")
    def student_detail(student_id):
        return "ok"

    return application


def response(status=201, payload=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    if payload is None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = payload
    return resp


class TestTheGate:
    def test_the_view_runs_when_the_service_says_yes(self, app):
        with app.test_request_context("/students/5"):
            with patch(AUTHENTICATE, return_value=response(201)):
                assert authenticated(lambda: "ok")() == "ok"

    def test_the_view_does_not_run_when_the_service_says_no(self, app):
        ran = []

        with app.test_request_context("/students/5"):
            with patch(AUTHENTICATE, return_value=response(401, text="denied")):
                with pytest.raises(UnauthorizedError):
                    authenticated(lambda: ran.append(True))()

        assert ran == []

    def test_the_view_does_not_run_when_the_service_is_unreachable(self, app):
        with app.test_request_context("/students/5"):
            with patch(AUTHENTICATE, return_value=None):
                with pytest.raises(UnauthorizedError, match="Authentication service unavailable"):
                    authenticated(lambda: "ok")()

    def test_the_rejection_carries_the_reason_from_the_body(self, app):
        with app.test_request_context("/students/5"):
            with patch(AUTHENTICATE, return_value=response(401, payload={"error": "unknown client"})):
                with pytest.raises(UnauthorizedError, match="unknown client"):
                    authenticated(lambda: "ok")()

    def test_the_rejection_falls_back_to_the_raw_body(self, app):
        with app.test_request_context("/students/5"):
            with patch(AUTHENTICATE, return_value=response(502, text="Bad Gateway")):
                with pytest.raises(UnauthorizedError, match="Bad Gateway"):
                    authenticated(lambda: "ok")()


class TestWhatIsSentToIntake:
    def test_sends_the_concrete_request_path(self, app):
        # Authentication asks "are these credentials valid", not "may they reach
        # this endpoint version", so unlike @authorized it does not need the
        # route pattern.
        with app.test_request_context("/students/5"):
            with patch(AUTHENTICATE, return_value=response(201)) as authenticate:
                authenticated(lambda: "ok")()

        assert authenticate.call_args[0][1] == "/students/5"

    def test_sends_the_detected_version(self, app):
        with app.test_request_context("/students/5", headers={"X-Api-Version": "v3"}):
            with patch(AUTHENTICATE, return_value=response(201)) as authenticate:
                authenticated(lambda: "ok")()

        assert authenticate.call_args[0][2] == "3"

    def test_sends_the_request_environ(self, app):
        with app.test_request_context("/students/5", headers={"Authorization": "Basic Y2xpZW50"}):
            with patch(AUTHENTICATE, return_value=response(201)) as authenticate:
                authenticated(lambda: "ok")()

        assert authenticate.call_args[0][0]["HTTP_AUTHORIZATION"] == "Basic Y2xpZW50"


class TestTheDecoratedView:
    def test_keeps_its_identity(self, app):
        @authenticated
        def student_detail():
            return "ok"

        assert student_detail.__name__ == "student_detail"

    def test_url_arguments_reach_the_view(self, app):
        @authenticated
        def view(student_id):
            return f"student {student_id}"

        with app.test_request_context("/students/5"):
            with patch(AUTHENTICATE, return_value=response(201)):
                assert view(student_id=5) == "student 5"
