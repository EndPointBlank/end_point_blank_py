"""
``@authenticated`` on a parameterized Flask route, over real HTTP, against a
stub intake that resolves the endpoint the way the real one does.

This is sc-342, closed loop and end to end. The SDK registers its own routes;
the stub stores what it was sent; the SDK then authenticates a request against
that same store. Nothing here tells the stub what to expect -- the registered
rows come from the SDK's own ``register_flask_endpoints``, so the two halves of
the SDK are checked against each other rather than against a fixture.

Against master the parameterized route is refused 403. ``@authenticated`` sent
``/students/5``; the registrar had published ``/students/{student_id}``; and
``Intake.Apis.get_endpoint/3`` matches ``path`` with SQL ``=``, so no row was
found. The refusal reads as a permissions failure, which is why this presents
as "my grant does not work" rather than "my path is wrong".

The static route passes against master too, and is here to show why the bug
could sit unnoticed: an application whose routes take no parameters sent the
right path by accident.

The stub reproduces exactly two behaviours of intake and no more:

- ``IntakeWeb.AuthorizationController`` runs the incoming ``path`` through
  ``Intake.PathNormalizer`` before anything else
  (``intake/lib/intake_web/controllers/authorization_controller.ex:20``), and
  registration runs the published path through the same normalizer
  (``intake/lib/intake/apis.ex:33``). Both sides, same function.
- ``Intake.Apis.get_endpoint/3`` then matches ``e.path == ^path`` in SQL
  (``intake/lib/intake/apis.ex:7-21``) -- no LIKE, no pattern matching, no
  trailing-slash tolerance.

``TestTheStubResolvesTheWayIntakeDoes`` pins both, so this file cannot quietly
become a test of its own stub.
"""

from __future__ import annotations

import json
import re
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
from end_point_blank.flask import authenticated, register_flask_endpoints
from end_point_blank.request_store import RequestStore
from end_point_blank.unauthorized_error import UnauthorizedError
from tests.intake_authorize import granted

# Intake's Intake.PathNormalizer, transcribed from
# intake/lib/intake/path_normalizer.ex. Canonical form is ":name". It rewrites
# "{name}" and "{name:regex}" and touches nothing else -- no leading slash, no
# trailing slash, no case folding, and no knowledge of "<int:name>".
_INTAKE_PATH_VAR = re.compile(r"\{([^/}:]+)(?::[^}]*)?\}")


def intake_normalize(path):
    return _INTAKE_PATH_VAR.sub(r":\1", path)


class StubIntake(BaseHTTPRequestHandler):
    """``POST /api/application_updates`` registers; ``POST /api/authorize``
    resolves against what was registered."""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/api/application_updates":
            for endpoint in payload.get("endpoints") or []:
                self.server.endpoints.add(
                    (intake_normalize(endpoint["path"]), endpoint["http_method"])
                )
            self.server.registrations.append(payload)
            return self._send(200, {"status": "ok"})

        self.server.bodies.append(payload)

        # AuthorizeAccess.authorize/1 pattern-matches http_method in every
        # clause head; a body without it never reaches endpoint resolution.
        if "http_method" not in payload:
            return self._send(401, {"authorized": False, "error": "invalid_params"})

        key = (intake_normalize(payload.get("path") or ""), payload["http_method"])
        if key not in self.server.endpoints:
            return self._send(403, {"authorized": False, "error": "access_denied"})

        return self._send(201, granted())

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
    """A provider's application: one parameterized route and one static route,
    both behind ``@authenticated``."""
    app = Flask("provider")

    @app.route("/students/<int:student_id>")
    @authenticated
    def student_detail(student_id):
        return jsonify(student_id=student_id)

    @app.route("/health")
    @authenticated
    def health():
        return jsonify(status="ok")

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
    def __init__(self, provider_url, intake_url, bodies, endpoints):
        self.provider = provider_url
        self.intake = intake_url
        self.bodies = bodies
        self.endpoints = endpoints

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
    intake.endpoints = set()
    intake.registrations = []

    app = provider_app()
    provider = make_server(
        "127.0.0.1", 0, app,
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
        # The SDK publishes its own routes, exactly as a provider would at
        # startup. Everything the stub can resolve, it learned here.
        with app.app_context():
            register_flask_endpoints(app)

        yield Live(
            f"http://127.0.0.1:{provider.server_port}",
            f"http://127.0.0.1:{intake.server_port}",
            intake.bodies,
            intake.endpoints,
        )

    AuthenticationCache().clear()
    RequestStore.clear()
    config._init_defaults()


class TestAParameterizedRoute:
    def test_the_caller_reaches_the_view(self, live):
        # Against master this is 403 access_denied: @authenticated sent
        # "/students/5" and no endpoint row is spelled that way.
        response = live.call()

        assert response.status_code == 200
        assert response.json() == {"student_id": 5}

    def test_the_path_on_the_wire_is_one_the_registrar_published(self, live):
        live.call()

        sent = live.bodies[-1]

        assert (intake_normalize(sent["path"]), sent["http_method"]) in live.endpoints

    def test_the_path_on_the_wire_is_the_pattern(self, live):
        live.call()

        assert live.bodies[-1]["path"] == "/students/{student_id}"

    def test_every_request_resolves_afresh(self, live):
        # Authentication is not cached, so the fix has to hold on every call
        # rather than on the first one that populated something.
        live.call()
        second = live.call()

        assert second.status_code == 200
        assert len(live.bodies) == 2


class TestAStaticRoute:
    """Passes against master too. It is here to show why the bug could sit
    unnoticed: with no parameter to lose, the concrete path and the pattern are
    the same string."""

    def test_the_caller_reaches_the_view(self, live):
        response = live.call("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_the_path_on_the_wire_is_one_the_registrar_published(self, live):
        live.call("/health")

        sent = live.bodies[-1]

        assert (intake_normalize(sent["path"]), sent["http_method"]) in live.endpoints


class TestTheStubResolvesTheWayIntakeDoes:
    """Guards on the stub itself, not regression tests -- all of these pass
    against master. Without them this file could pass by testing a stub that had
    been quietly taught to accept whatever the SDK now sends."""

    def test_the_registrar_reached_it(self, live):
        assert ("/students/:student_id", "GET") in live.endpoints
        assert ("/health", "GET") in live.endpoints

    def test_the_concrete_path_master_sent_is_refused_403(self, live):
        response = requests.post(
            f"{live.intake}/api/authorize",
            json={"path": "/students/5", "http_method": "GET", "client_auth": "Basic Y2xpZW50"},
            timeout=10,
        )

        assert response.status_code == 403
        assert response.json() == {"authorized": False, "error": "access_denied"}

    def test_the_registered_pattern_is_granted(self, live):
        response = requests.post(
            f"{live.intake}/api/authorize",
            json={
                "path": "/students/{student_id}",
                "http_method": "GET",
                "client_auth": "Basic Y2xpZW50",
            },
            timeout=10,
        )

        assert response.status_code == 201
        assert response.json()["authorized"] is True

    def test_it_matches_the_method_too(self, live):
        response = requests.post(
            f"{live.intake}/api/authorize",
            json={"path": "/students/{student_id}", "http_method": "DELETE"},
            timeout=10,
        )

        assert response.status_code == 403

    def test_a_body_without_an_http_method_never_reaches_resolution(self, live):
        response = requests.post(
            f"{live.intake}/api/authorize",
            json={"path": "/students/{student_id}"},
            timeout=10,
        )

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_params"
