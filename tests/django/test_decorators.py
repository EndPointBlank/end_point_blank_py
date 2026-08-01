"""
The Django ``@authenticated`` / ``@authorized`` decorators are what actually
denies a request. Two things matter: a non-201 must never fall through to the
view, and the path sent to intake must be the route pattern — intake matches
registered endpoints, and "/students/5" matches nothing.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from end_point_blank.django.decorators import authenticated, authorized
from end_point_blank.unauthorized_error import UnauthorizedError

AUTHENTICATE = "end_point_blank.commands.basic_authenticate.BasicAuthenticate.authenticate"
AUTHORIZE = "end_point_blank.commands.endpoint_authorize.EndpointAuthorize.authorize"


def django_request(route="students/<int:student_id>/", path="/students/5/"):
    request = RequestFactory().get(path)
    request.resolver_match = SimpleNamespace(route=route) if route is not None else None
    return request


def response(status=201, payload=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    if payload is None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = payload
    return resp


@pytest.fixture(params=[(authenticated, AUTHENTICATE, "Authentication"), (authorized, AUTHORIZE, "Authorization")],
                ids=["authenticated", "authorized"])
def decorator(request):
    """Both decorators share a contract; the differences are covered separately."""
    return request.param


class TestBothDecorators:
    def test_the_view_runs_when_the_service_says_yes(self, decorator):
        decorate, target, _ = decorator

        @decorate
        def view(request):
            return HttpResponse("ok")

        with patch(target, return_value=response(201)):
            assert view(django_request()).status_code == 200

    def test_the_view_does_not_run_when_the_service_says_no(self, decorator):
        decorate, target, _ = decorator
        ran = []

        @decorate
        def view(request):
            ran.append(True)
            return HttpResponse("ok")

        with patch(target, return_value=response(403, text="denied")):
            with pytest.raises(UnauthorizedError):
                view(django_request())

        assert ran == []

    def test_the_view_does_not_run_when_the_service_is_unreachable(self, decorator):
        # Failing closed is the whole point: an intake outage must not become an
        # open door on every protected endpoint.
        decorate, target, label = decorator

        @decorate
        def view(request):
            return HttpResponse("ok")

        with patch(target, return_value=None):
            with pytest.raises(UnauthorizedError, match=f"{label} service unavailable"):
                view(django_request())

    def test_the_rejection_carries_the_reason_from_the_body(self, decorator):
        decorate, target, _ = decorator

        @decorate
        def view(request):
            return HttpResponse("ok")

        with patch(target, return_value=response(403, payload={"error": "endpoint not registered"})):
            with pytest.raises(UnauthorizedError, match="endpoint not registered"):
                view(django_request())

    def test_the_rejection_falls_back_to_the_raw_body(self, decorator):
        decorate, target, _ = decorator

        @decorate
        def view(request):
            return HttpResponse("ok")

        with patch(target, return_value=response(502, text="Bad Gateway")):
            with pytest.raises(UnauthorizedError, match="Bad Gateway"):
                view(django_request())

    def test_the_view_keeps_its_identity(self, decorator):
        # Django resolves views by name in error pages and URL reversing, so the
        # decorator must not replace the view's name with "wrapper".
        decorate, _target, _ = decorator

        @decorate
        def student_detail(request):
            return HttpResponse("ok")

        assert student_detail.__name__ == "student_detail"

    def test_url_arguments_reach_the_view(self, decorator):
        decorate, target, _ = decorator

        @decorate
        def view(request, student_id):
            return HttpResponse(str(student_id))

        with patch(target, return_value=response(201)):
            assert view(django_request(), student_id=5).content == b"5"


class TestThePathSentToIntake:
    def test_uses_the_route_pattern_rather_than_the_matched_url(self):
        with patch(AUTHORIZE, return_value=response(201)) as authorize:
            authorized(lambda request: HttpResponse("ok"))(django_request())

        assert authorize.call_args[0][1] == "/students/{student_id}/"

    def test_normalizes_every_converter_form(self):
        request = django_request(route="classes/<int:class_id>/students/<slug:name>/")

        with patch(AUTHORIZE, return_value=response(201)) as authorize:
            authorized(lambda r: HttpResponse("ok"))(request)

        assert authorize.call_args[0][1] == "/classes/{class_id}/students/{name}/"

    def test_normalizes_a_converterless_parameter(self):
        request = django_request(route="students/<student_id>/")

        with patch(AUTHORIZE, return_value=response(201)) as authorize:
            authorized(lambda r: HttpResponse("ok"))(request)

        assert authorize.call_args[0][1] == "/students/{student_id}/"

    def test_falls_back_to_the_request_path_when_no_route_matched(self):
        request = django_request(route=None, path="/unrouted/thing")

        with patch(AUTHORIZE, return_value=response(201)) as authorize:
            authorized(lambda r: HttpResponse("ok"))(request)

        assert authorize.call_args[0][1] == "/unrouted/thing"

    def test_authenticate_receives_the_same_pattern(self):
        with patch(AUTHENTICATE, return_value=response(201)) as authenticate:
            authenticated(lambda r: HttpResponse("ok"))(django_request())

        assert authenticate.call_args[0][1] == "/students/{student_id}/"


class TestTheVersionSentToIntake:
    def test_the_detected_version_is_passed_along(self):
        # Authorization is decided per endpoint version, so a dropped version
        # authorizes against the wrong row.
        request = RequestFactory().get("/v2/students/5/", HTTP_X_API_VERSION="v2")
        request.resolver_match = SimpleNamespace(route="students/<int:student_id>/")

        with patch(AUTHORIZE, return_value=response(201)) as authorize:
            authorized(lambda r: HttpResponse("ok"))(request)

        assert authorize.call_args[0][2] == "2"
