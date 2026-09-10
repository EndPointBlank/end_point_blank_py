"""
``route_path`` is the one place the Flask integration decides what path to send
intake, and both decorators go through it.

They did not always. ``@authorized`` recovered the pattern from Werkzeug's URL
rule; ``@authenticated`` sent ``request.path``. The two sat in sibling files and
disagreed for as long as the disagreement went unnoticed, which was until
py#28 (sc-320) stopped intake refusing the authenticate call earlier for an
unrelated reason.

``TestTheTwoDecoratorsAgree`` is the test that makes the next divergence fail
here rather than in a customer's parameterized route.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, request

from end_point_blank.flask._route_path import route_path
from end_point_blank.flask.authenticated import authenticated
from end_point_blank.flask.authorized import authorized

AUTHENTICATE = "end_point_blank.commands.basic_authenticate.BasicAuthenticate.authenticate"
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

    @application.route("/health")
    def health():
        return "ok"

    return application


def granted():
    resp = MagicMock()
    resp.status_code = 201
    resp.text = ""
    resp.json.side_effect = ValueError("not json")
    return resp


class TestTheHelper:
    def test_normalizes_the_matched_rule(self, app):
        with app.test_request_context("/students/5"):
            assert route_path(request) == "/students/{student_id}"

    def test_normalizes_every_converter_form(self, app):
        with app.test_request_context("/classes/7/students/ada-lovelace"):
            assert route_path(request) == "/classes/{class_id}/students/{name}"

    def test_a_static_route_is_its_own_pattern(self, app):
        with app.test_request_context("/health"):
            assert route_path(request) == "/health"

    def test_falls_back_to_the_request_path_when_no_rule_matched(self, app):
        with app.test_request_context("/no/such/route"):
            assert route_path(request) == "/no/such/route"

    def test_falls_back_when_the_object_has_no_rule_attribute_at_all(self):
        # A request-like object from outside Werkzeug's routing, e.g. a context
        # pushed by hand in a test or a script.
        assert route_path(MagicMock(spec=["path"], path="/students/5")) == "/students/5"


class TestTheTwoDecoratorsAgree:
    """Same request, same path on the wire. Intake resolves the endpoint row
    the same way for both calls -- ``Intake.Apis.get_endpoint/3``, matching
    ``path`` with SQL ``=`` -- so a request that authorizes but cannot
    authenticate is a bug in this SDK, not a grant the caller is missing."""

    @pytest.mark.parametrize(
        "url",
        [
            "/students/5",
            "/classes/7/students/ada-lovelace",
            "/health",
            "/no/such/route",
        ],
    )
    def test_both_decorators_send_the_same_path(self, app, url):
        with app.test_request_context(url):
            with patch(AUTHENTICATE, return_value=granted()) as authenticate:
                authenticated(lambda: "ok")()
            with patch(AUTHORIZE, return_value=granted()) as authorize:
                authorized(lambda: "ok")()

        assert authenticate.call_args[0][1] == authorize.call_args[0][1]

    def test_and_that_path_is_the_pattern(self, app):
        # Agreement alone would be satisfied by both of them being wrong.
        with app.test_request_context("/students/5"):
            with patch(AUTHENTICATE, return_value=granted()) as authenticate:
                authenticated(lambda: "ok")()

        assert authenticate.call_args[0][1] == "/students/{student_id}"
