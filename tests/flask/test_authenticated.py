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

    @application.route("/classes/<class_id>/students/<string:name>")
    def nested(class_id, name):
        return "ok"

    @application.route("/health")
    def health():
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
    def test_sends_the_route_pattern_rather_than_the_concrete_path(self, app):
        # This file used to assert "/students/5", on the reasoning that
        # authentication asks "are these credentials valid" rather than "may
        # they reach this endpoint version", and so does not need the pattern.
        # Intake disagrees: it resolves an endpoint row before it considers the
        # credential, and Intake.Apis.get_endpoint/3 matches path with SQL "=".
        # "/students/5" is not a row, so the caller was refused for a grant they
        # held -- and the refusal read as a permissions problem, not a path one.
        with app.test_request_context("/students/5"):
            with patch(AUTHENTICATE, return_value=response(201)) as authenticate:
                authenticated(lambda: "ok")()

        assert authenticate.call_args[0][1] == "/students/{student_id}"

    def test_normalizes_every_converter_form(self, app):
        with app.test_request_context("/classes/7/students/ada-lovelace"):
            with patch(AUTHENTICATE, return_value=response(201)) as authenticate:
                authenticated(lambda: "ok")()

        assert authenticate.call_args[0][1] == "/classes/{class_id}/students/{name}"

    def test_a_static_route_is_sent_unchanged(self, app):
        # The case that hid the bug: an application whose routes take no
        # parameters sent the right thing by accident, and nothing surfaced.
        with app.test_request_context("/health"):
            with patch(AUTHENTICATE, return_value=response(201)) as authenticate:
                authenticated(lambda: "ok")()

        assert authenticate.call_args[0][1] == "/health"

    def test_falls_back_to_the_request_path_when_no_rule_matched(self, app):
        # 404s and manually-pushed contexts have no url_rule; sending the raw
        # path is better than sending nothing.
        with app.test_request_context("/no/such/route"):
            with patch(AUTHENTICATE, return_value=response(201)) as authenticate:
                authenticated(lambda: "ok")()

        assert authenticate.call_args[0][1] == "/no/such/route"

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


class TestTheStatusOnTheRefusal:
    """Intake's verdict has to reach the application's error handler, which
    renders the refusal off the error. While the error carried no status, every
    refusal a Flask provider rendered was a 401 -- including intake's 403."""

    def test_a_denied_grant_arrives_as_403(self, app):
        with app.test_request_context("/protected"):
            with patch(AUTHENTICATE, return_value=response(403, payload={"error": "access_denied"})):
                with pytest.raises(UnauthorizedError) as raised:
                    authenticated(lambda: "ok")()

        assert raised.value.status_code == 403

    def test_a_rejected_credential_arrives_as_401(self, app):
        with app.test_request_context("/protected"):
            with patch(AUTHENTICATE, return_value=response(401, payload={"error": "invalid_client"})):
                with pytest.raises(UnauthorizedError) as raised:
                    authenticated(lambda: "ok")()

        assert raised.value.status_code == 401

    def test_an_unreachable_service_is_a_503_rather_than_a_401(self, app):
        # Nothing refused the caller; the check could not be made. Blaming the
        # credential would send them to re-issue a credential that is fine.
        with app.test_request_context("/protected"):
            with patch(AUTHENTICATE, return_value=None):
                with pytest.raises(UnauthorizedError) as raised:
                    authenticated(lambda: "ok")()

        assert raised.value.status_code == 503

    def test_an_intake_failure_keeps_its_own_status(self, app):
        with app.test_request_context("/protected"):
            with patch(AUTHENTICATE, return_value=response(502, text="Bad Gateway")):
                with pytest.raises(UnauthorizedError) as raised:
                    authenticated(lambda: "ok")()

        assert raised.value.status_code == 502
