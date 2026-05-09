from unittest.mock import patch
import pytest

flask = pytest.importorskip("flask")
from flask import Flask
from end_point_blank.flask import register_flask_endpoints, versioned


def make_app():
    app = Flask(__name__)

    @app.route("/api/v1/users", methods=["GET"])
    @versioned(["v1", "v2"], state="Current")
    def list_users():
        return []

    @app.route("/api/v1/users/<int:user_id>", methods=["GET", "DELETE"])
    def get_user(user_id):
        return {}

    return app


def test_collects_endpoints():
    app = make_app()
    with patch("end_point_blank.commands.endpoint_update.EndpointUpdate.send_update") as mock_update:
        with app.app_context():
            register_flask_endpoints(app)

        endpoints = mock_update.call_args[0][0]
        paths = [e["path"] for e in endpoints]
        assert "/api/v1/users" in paths


def test_versioned_metadata_included():
    app = make_app()
    with patch("end_point_blank.commands.endpoint_update.EndpointUpdate.send_update") as mock_update:
        with app.app_context():
            register_flask_endpoints(app)

        endpoints = mock_update.call_args[0][0]
        versioned_endpoint = next(
            (e for e in endpoints if e["path"] == "/api/v1/users" and e["action"] == "GET"),
            None,
        )
        assert versioned_endpoint is not None
        assert versioned_endpoint["versions"]["Current"] == ["v1", "v2"]


def test_skips_static_endpoint():
    app = make_app()
    with patch("end_point_blank.commands.endpoint_update.EndpointUpdate.send_update") as mock_update:
        with app.app_context():
            register_flask_endpoints(app)

        endpoints = mock_update.call_args[0][0]
        assert not any(e["path"] == "/static" for e in endpoints)
