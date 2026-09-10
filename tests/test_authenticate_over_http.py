"""
``@authenticated`` against a stub intake that refuses the way the real one does,
over real HTTP on loopback.

The bug this pins could not be caught by a double. ``BasicAuthenticate`` sent
the request method as ``action``; ``tests/commands/test_basic_authenticate.py``
asserted ``body["action"] == "GET"`` against a ``MagicMock``, and passed --
because a mock agrees with whatever it is handed. The only thing that
disagrees is a reader, and intake is the reader.

So there is no mock here. A real Flask application on this branch's ``src``,
with the real ``@authenticated`` decorator on a real route, is served over a
real socket; the SDK's real ``requests`` call reaches a stub intake on a second
socket; and the assertion is on the status a real client got back.

The stub reproduces exactly one behaviour of ``AuthorizeAccess.authorize/1``:
every clause head pattern-matches ``http_method``, so a body without that key
falls through to

    def authorize(_params), do: {:error, :invalid_params}

at ``intake/lib/intake/authorize_access.ex:88-90``, and
``IntakeWeb.AuthorizationController`` renders 401 with
``{"authorized": false, "error": "invalid_params"}``. It refuses on the *key's
absence*, never on a list of spellings it has been told to dislike -- a body may
carry any other keys it likes, exactly as intake ignores every key it does not
read. ``TestTheStubRefusesTheWayIntakeDoes`` pins that, so this file cannot
quietly become a test of its own stub.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pytest
import requests
from flask import Flask, jsonify

from end_point_blank.commands.authentication_cache import AuthenticationCache
from end_point_blank.configuration import Configuration
from end_point_blank.flask import authenticated
from end_point_blank.request_store import RequestStore
from end_point_blank.unauthorized_error import UnauthorizedError

# The exact body master built, key for key. Kept whole rather than reduced to
# the three names that changed, so the stub is shown accepting or refusing a
# body a real deployment would have sent.
MASTER_BODY = {
    "path": "/students/5",
    "action": "GET",
    "client_auth": "Basic Y2xpZW50",
    "application": "students-api",
    "version": "1",
    "ip_address": "127.0.0.1",
}


class StubIntake(BaseHTTPRequestHandler):
    """``POST /api/authorize``, refusing on a missing ``http_method``."""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.server.bodies.append(payload)

        if "http_method" not in payload:
            self._send(401, {"authorized": False, "error": "invalid_params"})
        else:
            self._send(201, {"authorized": True, "data": [{"id": "acc-1"}]})

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        """Silence the default stderr access log."""


class QuietWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, *_args):
        """Silence the default stderr access log."""


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """The provider has to answer the client while the SDK is mid-call to the
    stub, so a single-threaded server would deadlock against itself."""

    daemon_threads = True


def provider_app():
    """A provider's application: one route behind ``@authenticated``.

    The refusal handler is what ``epb_test_py/json_error_middleware.py`` does
    with an ``UnauthorizedError``, transcribed rather than imported -- that
    application is Django, and nothing here may modify it.
    """
    app = Flask("provider")

    @app.route("/students/<int:student_id>")
    @authenticated
    def student_detail(student_id):
        return jsonify(student_id=student_id)

    @app.errorhandler(UnauthorizedError)
    def refusal(exception):
        return jsonify(error=str(exception)), getattr(exception, "status_code", 401)

    return app


@contextmanager
def _running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class Live:
    def __init__(self, provider_url, intake_url, bodies):
        self.provider = provider_url
        self.intake = intake_url
        self.bodies = bodies

    def call(self, path="/students/5", **headers):
        sent = {"Authorization": "Basic Y2xpZW50", "X-Api-Version": "v1"}
        sent.update(headers)
        return requests.get(f"{self.provider}{path}", headers=sent, timeout=10)


@pytest.fixture
def live(monkeypatch):
    for key in ("ENDPOINTBLANK_BASE_URL", "ENDPOINTBLANK_APP_NAME"):
        monkeypatch.delenv(key, raising=False)

    intake = ThreadingHTTPServer(("127.0.0.1", 0), StubIntake)
    intake.daemon_threads = True
    intake.bodies = []
    provider = make_server(
        "127.0.0.1", 0, provider_app(),
        server_class=ThreadingWSGIServer, handler_class=QuietWSGIRequestHandler,
    )

    config = Configuration()
    config._init_defaults()
    config.base_url = f"http://127.0.0.1:{intake.server_port}"
    config.app_name = "students-api"
    config.client_id = "provider-id"
    config.client_secret = "provider-secret"
    AuthenticationCache().clear()
    RequestStore.clear()

    with _running(intake), _running(provider):
        yield Live(
            f"http://127.0.0.1:{provider.server_port}",
            f"http://127.0.0.1:{intake.server_port}",
            intake.bodies,
        )

    AuthenticationCache().clear()
    RequestStore.clear()
    config._init_defaults()


class TestTheAuthenticatedRoute:
    def test_the_caller_reaches_the_view(self, live):
        # The whole story in one line. Against master this is 401: intake never
        # saw an http_method, so nothing it could have granted mattered.
        response = live.call()

        assert response.status_code == 200
        assert response.json() == {"student_id": 5}

    def test_the_view_is_still_refused_when_intake_says_no(self, live):
        # A guard, not a regression test: it passes against master too, where
        # the omission is what the command did anyway. It is here so the 200
        # above cannot be a decorator that stopped checking -- when the stub
        # refuses, the caller is refused.
        from end_point_blank.commands import basic_authenticate as module

        original = module.post

        def without_http_method(url, auth, body):
            return original(url, auth, {k: v for k, v in body.items() if k != "http_method"})

        module.post = without_http_method
        try:
            response = live.call()
        finally:
            module.post = original

        assert response.status_code == 401
        assert "invalid_params" in response.json()["error"]

    def test_intake_receives_the_names_it_reads(self, live):
        live.call()

        assert len(live.bodies) == 1
        body = live.bodies[0]

        assert body["http_method"] == "GET"
        assert body["client_auth"] == "Basic Y2xpZW50"
        assert body["endpoint_version"] == "1"
        assert body["source_ip"] == "127.0.0.1"
        # The route pattern, not "/students/5". Intake resolves the endpoint row
        # by an exact match on this, so the concrete URL finds nothing (sc-342).
        assert body["path"] == "/students/{student_id}"

    @pytest.mark.parametrize("key", ["action", "version", "ip_address"])
    def test_intake_receives_none_of_the_names_it_ignores(self, live, key):
        live.call()

        assert key not in live.bodies[0]

    def test_every_request_is_authenticated_afresh(self, live):
        # Authentication is not cached, so the fix has to hold on every call
        # rather than on the first one that populated something.
        live.call()
        second = live.call()

        assert second.status_code == 200
        assert len(live.bodies) == 2
        assert all("http_method" in body for body in live.bodies)


class TestTheStubRefusesTheWayIntakeDoes:
    """Guards on the stub itself, not regression tests -- both pass against
    master. Without them this file could pass by testing a stub that had been
    quietly taught to accept whatever the SDK now sends.
    """

    def test_the_body_master_sent_is_refused_401(self, live):
        response = requests.post(f"{live.intake}/api/authorize", json=MASTER_BODY, timeout=10)

        assert response.status_code == 401
        assert response.json() == {"authorized": False, "error": "invalid_params"}

    def test_the_same_body_plus_http_method_is_accepted(self, live):
        # Every unread spelling still present -- action, version, ip_address --
        # and it is granted. So the refusal above was about http_method being
        # absent and nothing else, which is how intake behaves: it ignores what
        # it does not read rather than objecting to it.
        response = requests.post(
            f"{live.intake}/api/authorize",
            json={**MASTER_BODY, "http_method": "GET"},
            timeout=10,
        )

        assert response.status_code == 201
        assert response.json()["authorized"] is True
