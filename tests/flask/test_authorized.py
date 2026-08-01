"""
``@authorized`` is the Flask gate. It has one job the Django equivalent does not
have to work for: recovering the route pattern from Werkzeug's URL rule, because
intake matches registered endpoints and the concrete URL matches nothing.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from end_point_blank.flask.authorized import authorized
from end_point_blank.unauthorized_error import UnauthorizedError

AUTHORIZE = "end_point_blank.commands.endpoint_authorize.EndpointAuthorize.authorize"


@pytest.fixture
def app():
    application = Flask(__name__)

    @application.route("/students/<int:student_id>")
    def student_detail(student_id):
        return "ok"

    @application.route("/classes/<class_id>/students/<string:name>")
    def nested(class_id, name):
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
            with patch(AUTHORIZE, return_value=response(201)):
                assert authorized(lambda: "ok")() == "ok"

    def test_the_view_does_not_run_when_the_service_says_no(self, app):
        ran = []

        with app.test_request_context("/students/5"):
            with patch(AUTHORIZE, return_value=response(403, text="denied")):
                with pytest.raises(UnauthorizedError):
                    authorized(lambda: ran.append(True))()

        assert ran == []

    def test_the_view_does_not_run_when_the_service_is_unreachable(self, app):
        # Failing closed is the whole point: an intake outage must not become an
        # open door on every protected endpoint.
        with app.test_request_context("/students/5"):
            with patch(AUTHORIZE, return_value=None):
                with pytest.raises(UnauthorizedError, match="Authorization service unavailable"):
                    authorized(lambda: "ok")()

    def test_any_non_201_is_a_rejection(self, app):
        # A 200 from the authorize endpoint means something other than "granted";
        # only 201 is the grant, so anything else must not fall through.
        with app.test_request_context("/students/5"):
            with patch(AUTHORIZE, return_value=response(200)):
                with pytest.raises(UnauthorizedError):
                    authorized(lambda: "ok")()

    def test_the_rejection_carries_the_reason_from_the_body(self, app):
        with app.test_request_context("/students/5"):
            with patch(AUTHORIZE, return_value=response(403, payload={"error": "endpoint not registered"})):
                with pytest.raises(UnauthorizedError, match="endpoint not registered"):
                    authorized(lambda: "ok")()

    def test_the_rejection_falls_back_to_the_raw_body(self, app):
        with app.test_request_context("/students/5"):
            with patch(AUTHORIZE, return_value=response(502, text="Bad Gateway")):
                with pytest.raises(UnauthorizedError, match="Bad Gateway"):
                    authorized(lambda: "ok")()


class TestThePathSentToIntake:
    def test_uses_the_route_pattern_rather_than_the_matched_url(self, app):
        with app.test_request_context("/students/5"):
            with patch(AUTHORIZE, return_value=response(201)) as authorize:
                authorized(lambda: "ok")()

        assert authorize.call_args[0][1] == "/students/{student_id}"

    def test_normalizes_every_converter_form(self, app):
        with app.test_request_context("/classes/7/students/ada-lovelace"):
            with patch(AUTHORIZE, return_value=response(201)) as authorize:
                authorized(lambda: "ok")()

        assert authorize.call_args[0][1] == "/classes/{class_id}/students/{name}"

    def test_falls_back_to_the_request_path_when_no_rule_matched(self, app):
        # 404s and manually-pushed contexts have no url_rule; sending the raw
        # path is better than sending nothing.
        with app.test_request_context("/no/such/route"):
            with patch(AUTHORIZE, return_value=response(201)) as authorize:
                authorized(lambda: "ok")()

        assert authorize.call_args[0][1] == "/no/such/route"


class TestTheVersionSentToIntake:
    def test_the_detected_version_is_passed_along(self, app):
        # Authorization is decided per endpoint version, so a dropped version
        # authorizes against the wrong row.
        with app.test_request_context("/students/5", headers={"X-Api-Version": "v2"}):
            with patch(AUTHORIZE, return_value=response(201)) as authorize:
                authorized(lambda: "ok")()

        assert authorize.call_args.kwargs["version"] == "2"

    def test_no_detectable_version_is_sent_as_null(self, app):
        with app.test_request_context("/students/5"):
            with patch(AUTHORIZE, return_value=response(201)) as authorize:
                authorized(lambda: "ok")()

        assert authorize.call_args.kwargs["version"] is None


class TestTheDecoratedView:
    def test_keeps_its_identity(self, app):
        # Flask registers views by __name__; a wrapper that loses it collides
        # with every other decorated view in the app.
        @authorized
        def student_detail():
            return "ok"

        assert student_detail.__name__ == "student_detail"

    def test_url_arguments_reach_the_view(self, app):
        @authorized
        def view(student_id):
            return f"student {student_id}"

        with app.test_request_context("/students/5"):
            with patch(AUTHORIZE, return_value=response(201)):
                assert view(student_id=5) == "student 5"
