"""
``BasicAuthenticate`` backs the ``@authenticated`` decorators in both framework
integrations. Unlike ``EndpointAuthorize`` it does not cache, so every field it
sends is sent on every request — a wrong key is a per-request failure, not an
occasional one.
"""

from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.commands import basic_authenticate as ba
from end_point_blank.commands.basic_authenticate import BasicAuthenticate
from end_point_blank.configuration import Configuration


def environ(**overrides):
    base = {
        "HTTP_AUTHORIZATION": "Basic Y2xpZW50",
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/students/5",
        "REMOTE_ADDR": "10.0.0.1",
    }
    base.update(overrides)
    return base


def response(status=201, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    for key in ("ENDPOINTBLANK_BASE_URL", "ENDPOINTBLANK_APP_NAME"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    config.base_url = "https://intake.test"
    config.app_name = "students-api"
    yield config
    config._init_defaults()


class TestTheRequest:
    def test_posts_to_the_configured_authorize_url(self):
        with patch.object(ba, "post", return_value=response()) as post:
            BasicAuthenticate.authenticate(environ(), "/students/{id}", "1")

        assert post.call_args[0][0] == "https://intake.test/api/authorize"

    def test_sends_the_details_intake_matches_on(self):
        with patch.object(ba, "post", return_value=response()) as post:
            BasicAuthenticate.authenticate(environ(), "/students/{id}", "1")

        body = post.call_args[0][2]

        assert body["path"] == "/students/{id}"
        assert body["action"] == "GET"
        assert body["client_auth"] == "Basic Y2xpZW50"
        assert body["application"] == "students-api"
        assert body["version"] == "1"

    def test_sends_the_route_pattern_rather_than_the_concrete_path(self):
        # Intake matches a registered endpoint row; "/students/5" would match
        # nothing, so the caller's pattern has to win over PATH_INFO.
        with patch.object(ba, "post", return_value=response()) as post:
            BasicAuthenticate.authenticate(environ(), "/students/{id}", "1")

        assert post.call_args[0][2]["path"] == "/students/{id}"

    def test_carries_a_null_version_when_none_was_detected(self):
        with patch.object(ba, "post", return_value=response()) as post:
            BasicAuthenticate.authenticate(environ(), "/students", None)

        assert post.call_args[0][2]["version"] is None


class TestTheClientAddress:
    def test_uses_remote_addr_by_default(self):
        with patch.object(ba, "post", return_value=response()) as post:
            BasicAuthenticate.authenticate(environ(), "/students", "1")

        assert post.call_args[0][2]["ip_address"] == "10.0.0.1"

    def test_prefers_the_first_forwarded_address(self):
        env = environ(HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")

        with patch.object(ba, "post", return_value=response()) as post:
            BasicAuthenticate.authenticate(env, "/students", "1")

        assert post.call_args[0][2]["ip_address"] == "203.0.113.7"

    def test_an_explicit_override_wins_over_the_environ(self):
        env = environ(HTTP_X_FORWARDED_FOR="203.0.113.7")

        with patch.object(ba, "post", return_value=response()) as post:
            BasicAuthenticate.authenticate(env, "/students", "1", ip_address="198.51.100.4")

        assert post.call_args[0][2]["ip_address"] == "198.51.100.4"

    def test_is_null_when_the_environ_carries_no_address(self):
        env = environ()
        env.pop("REMOTE_ADDR")

        with patch.object(ba, "post", return_value=response()) as post:
            BasicAuthenticate.authenticate(env, "/students", "1")

        assert post.call_args[0][2]["ip_address"] is None


class TestTheResult:
    def test_returns_the_response_on_success(self):
        with patch.object(ba, "post", return_value=response(201)) as post:
            result = BasicAuthenticate.authenticate(environ(), "/students", "1")

        assert result is post.return_value
        assert result.status_code == 201

    def test_returns_none_when_intake_is_unreachable(self):
        with patch.object(ba, "post", return_value=None):
            assert BasicAuthenticate.authenticate(environ(), "/students", "1") is None

    @pytest.mark.parametrize("status", [401, 403, 404, 500])
    def test_returns_a_rejection_rather_than_raising(self, status):
        # The decorator turns a non-201 into an UnauthorizedError carrying the
        # body; flattening failures to None here would lose that message.
        with patch.object(ba, "post", return_value=response(status, text="denied")):
            result = BasicAuthenticate.authenticate(environ(), "/students", "1")

        assert result.status_code == status
        assert result.text == "denied"

    def test_does_not_cache_between_calls(self):
        # Authentication is per-request by design: unlike authorization there is
        # no cache, so a revoked credential stops working immediately.
        with patch.object(ba, "post", return_value=response()) as post:
            BasicAuthenticate.authenticate(environ(), "/students", "1")
            BasicAuthenticate.authenticate(environ(), "/students", "1")

        assert post.call_count == 2
