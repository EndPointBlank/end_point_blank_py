"""
The names every command that POSTs to intake's ``/api/authorize`` must send its
request under, asserted against all of them from one list.

This exists because of a real bug. ``BasicAuthenticate`` and ``EndpointAuthorize``
post to the *same URL*, so intake reads one set of keys out of both bodies -- but
each command spelled that body out for itself. ``EndpointAuthorize`` sent
``http_method``, ``endpoint_version`` and ``source_ip``; ``BasicAuthenticate``
sent ``action``, ``version`` and ``ip_address``, none of which intake reads.
Every clause of ``AuthorizeAccess.authorize/1`` pattern-matches ``http_method``,
so a body without it fell through to the catch-all and the controller rendered
401 -- meaning the authenticate path could never succeed, whatever credential it
presented.

Neither command's own test file could have caught it. Each asserted the payload
its own command built, against an in-process double, so ``BasicAuthenticate``
was free to be internally consistent about a name intake had never heard of and
still look green. A test that asks a mock "did you receive what I sent you" only
ever asks that.

The fix is structural, and it is the same one ``test_integration_contract.py``
applies across framework integrations: one list of names, parametrized over
every command that talks to this endpoint. Adding a command means adding an
adapter below, and this list becomes the checklist it has to satisfy.

What proves the names are the *right* ones is not here -- a shared list can be
shared and wrong. That proof is in ``test_authenticate_over_http.py``, which
puts a real request through a stub that refuses a body without ``http_method``
the way intake does.
"""

from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.commands.authentication_cache import AuthenticationCache
from end_point_blank.configuration import Configuration
from end_point_blank.request_store import RequestStore
from tests.intake_authorize import granted

# What intake actually reads on POST /api/authorize. Every other key in the body
# is ignored:
#
#   client_auth       intake/lib/intake_web/controllers/authorization_controller.ex:17
#   path              normalized at authorization_controller.ex:20; matched at
#                     intake/lib/intake/authorize_access.ex:16 and :61; stored at
#                     intake/lib/intake/authorizations.ex:135
#   http_method       matched at authorize_access.ex:17 and :62; stored at
#                     authorizations.ex:136
#   endpoint_version  authorization_controller.ex:60, for the deprecation
#                     lookup; stored at authorizations.ex:137
#   source_ip         stored at authorizations.ex:141 as source_ip_address
READ_BY_INTAKE = ("client_auth", "path", "http_method", "endpoint_version", "source_ip")

# Spellings intake has never read, each of which one command sent and the other
# did not. They are named individually rather than inferred from "not in
# READ_BY_INTAKE" because two of them cost nothing visible -- a null column --
# and a rule that only fails loudly is a rule that gets rediscovered.
IGNORED_BY_INTAKE = ("action", "version", "ip_address")


def environ(**overrides):
    base = {
        "HTTP_AUTHORIZATION": "Basic Y2xpZW50",
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/students/5",
        "REMOTE_ADDR": "10.0.0.1",
        "HTTP_HOST": "students.test",
    }
    base.update(overrides)
    return base


def _accepted():
    """An authorize response intake would have sent for a granted request."""
    response = MagicMock()
    response.status_code = 201
    response.text = ""
    response.json.return_value = granted()
    return response


class BasicAuthenticateCommand:
    """The command behind ``@authenticated`` in both framework integrations."""

    id = "basic_authenticate"

    def send(self, path="/students/{id}", version="1", env=None):
        from end_point_blank.commands import basic_authenticate as module
        from end_point_blank.commands.basic_authenticate import BasicAuthenticate

        with patch.object(module, "post", return_value=_accepted()) as post:
            BasicAuthenticate.authenticate(env or environ(), path, version)

        return post.call_args[0]


class EndpointAuthorizeCommand:
    """The command behind ``@authorized`` in both framework integrations."""

    id = "endpoint_authorize"

    def send(self, path="/students/{id}", version="1", env=None):
        from end_point_blank.commands import endpoint_authorize as module
        from end_point_blank.commands.endpoint_authorize import EndpointAuthorize

        with patch.object(module, "post", return_value=_accepted()) as post:
            EndpointAuthorize.authorize(env or environ(), path, version)

        return post.call_args[0]


COMMANDS = [
    pytest.param(BasicAuthenticateCommand(), id=BasicAuthenticateCommand.id),
    pytest.param(EndpointAuthorizeCommand(), id=EndpointAuthorizeCommand.id),
]


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    for key in ("ENDPOINTBLANK_BASE_URL", "ENDPOINTBLANK_APP_NAME"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    config.base_url = "https://intake.test"
    config.app_name = "students-api"
    # EndpointAuthorize caches a granted result; a hit would return before it
    # ever built a body, so every case here would silently assert the previous
    # case's payload.
    AuthenticationCache().clear()
    RequestStore.clear()
    yield config
    AuthenticationCache().clear()
    RequestStore.clear()
    config._init_defaults()


@pytest.mark.parametrize("command", COMMANDS)
class TestTheNamesIntakeReads:
    """Parametrized over both commands, so half of these cases are guards
    rather than regression tests: the ``endpoint_authorize`` half already
    passed before ``basic_authenticate`` was fixed. That is the point -- the
    side that was right is now pinned too, so the next divergence cannot be
    achieved by breaking the other one.
    """

    @pytest.mark.parametrize("key", READ_BY_INTAKE)
    def test_sends_every_key_intake_reads(self, command, key):
        _url, _auth, body = command.send()

        assert key in body, f"{command.id} sends no {key!r}; intake reads it"

    @pytest.mark.parametrize("key", IGNORED_BY_INTAKE)
    def test_sends_no_key_intake_ignores(self, command, key):
        _url, _auth, body = command.send()

        assert key not in body, f"{command.id} sends {key!r}; intake never reads it"

    def test_http_method_carries_the_request_method(self, command):
        _url, _auth, body = command.send(env=environ(REQUEST_METHOD="DELETE"))

        assert body["http_method"] == "DELETE"

    def test_endpoint_version_carries_the_detected_version(self, command):
        _url, _auth, body = command.send(version="3")

        assert body["endpoint_version"] == "3"

    def test_source_ip_carries_the_client_address(self, command):
        _url, _auth, body = command.send(env=environ(REMOTE_ADDR="203.0.113.7"))

        assert body["source_ip"] == "203.0.113.7"

    def test_path_carries_the_route_pattern_it_was_given(self, command):
        _url, _auth, body = command.send(path="/classes/{class_id}/students")

        assert body["path"] == "/classes/{class_id}/students"

    def test_client_auth_carries_the_callers_own_header(self, command):
        # Not this service's credential -- that goes in the Authorization
        # header. client_auth is who is calling *us*.
        _url, auth, body = command.send()

        assert body["client_auth"] == "Basic Y2xpZW50"
        assert auth != body["client_auth"]


class TestTheTwoCommandsAgree:
    """The reason the list above has to be shared rather than duplicated."""

    def test_both_post_to_the_same_url(self):
        authenticate_url = BasicAuthenticateCommand().send()[0]
        authorize_url = EndpointAuthorizeCommand().send()[0]

        assert authenticate_url == authorize_url == "https://intake.test/api/authorize"

    @pytest.mark.parametrize("key", READ_BY_INTAKE)
    def test_they_spell_the_same_field_the_same_way(self, key):
        # One endpoint, one reader, one spelling. Divergence here is the whole
        # bug: the same value under two names, one of which is dropped on the
        # floor.
        authenticate_body = BasicAuthenticateCommand().send()[2]
        authorize_body = EndpointAuthorizeCommand().send()[2]

        assert key in authenticate_body and key in authorize_body

    def test_neither_carries_a_field_the_other_calls_something_else(self):
        authenticate_body = BasicAuthenticateCommand().send()[2]
        authorize_body = EndpointAuthorizeCommand().send()[2]

        divergent = set(IGNORED_BY_INTAKE) & (set(authenticate_body) | set(authorize_body))
        assert divergent == set(), f"unread spellings still on the wire: {sorted(divergent)}"
