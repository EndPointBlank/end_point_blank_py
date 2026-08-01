"""
``EndpointUpdate`` publishes the endpoint manifest at boot. It runs once per
deploy, in the app's startup path, so a raise here is a failed deploy rather
than a lost telemetry row.
"""

import socket
from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.commands import endpoint_update as eu
from end_point_blank.commands.endpoint_update import EndpointUpdate
from end_point_blank.configuration import Configuration

ENDPOINTS = [{"path": "/students", "http_method": "GET", "endpoint_versions": ["v1"]}]


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    for key in ("ENDPOINTBLANK_BASE_URL", "ENDPOINTBLANK_APP_NAME", "ENDPOINTBLANK_ENV"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    config.base_url = "https://intake.test"
    config.app_name = "students-api"
    config.environment = "production"
    yield config
    config._init_defaults()


def response(status=201, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


def send_and_capture(endpoints=None):
    with patch.object(eu, "post", return_value=response()) as post:
        EndpointUpdate.send_update(endpoints)
    return post.call_args


class TestThePayload:
    def test_posts_to_the_configured_update_url(self):
        assert send_and_capture(ENDPOINTS)[0][0] == "https://intake.test/api/application_updates"

    def test_carries_the_endpoint_list(self):
        assert send_and_capture(ENDPOINTS)[0][2]["endpoints"] == ENDPOINTS

    def test_carries_the_application_identity(self):
        body = send_and_capture(ENDPOINTS)[0][2]

        assert body["application"] == "students-api"
        assert body["environment"] == "production"

    def test_reports_the_hostname_of_the_instance(self):
        # The portal shows which instances have reported in; without a hostname
        # a partial rollout is indistinguishable from a complete one.
        assert send_and_capture(ENDPOINTS)[0][2]["hostname"] == socket.gethostname()

    def test_reports_the_client_library_version(self):
        assert send_and_capture(ENDPOINTS)[0][2]["lib_version"] == eu.VERSION

    def test_reports_the_configured_application_version(self, _config):
        _config.application_version = "2026.07.31"

        assert send_and_capture(ENDPOINTS)[0][2]["app_version"] == "2026.07.31"

    def test_authenticates_the_request(self):
        assert send_and_capture(ENDPOINTS)[0][1].startswith("Basic ")

    def test_publishes_an_empty_manifest_when_no_endpoints_are_given(self):
        # Sending [] is how an app says it now serves nothing; skipping the call
        # would leave stale endpoint rows visible in the portal forever.
        assert send_and_capture()[0][2]["endpoints"] == []

    def test_the_instance_form_sends_the_same_payload(self):
        with patch.object(eu, "post", return_value=response()) as post:
            EndpointUpdate(ENDPOINTS).update()

        assert post.call_args[0][2]["endpoints"] == ENDPOINTS


class TestTheHostname:
    def test_falls_back_when_the_hostname_cannot_be_resolved(self):
        with patch.object(eu.socket, "gethostname", side_effect=OSError("no hostname")):
            body = send_and_capture(ENDPOINTS)[0][2]

        assert body["hostname"] == "localhost"


class TestFailureIsNotFatal:
    def test_an_unreachable_intake_does_not_break_startup(self):
        # This runs during application boot; raising would turn a telemetry
        # outage into a failed deploy.
        with patch.object(eu, "post", return_value=None):
            EndpointUpdate.send_update(ENDPOINTS)

    @pytest.mark.parametrize("status", [401, 422, 500])
    def test_a_rejected_manifest_does_not_break_startup(self, status):
        with patch.object(eu, "post", return_value=response(status, text="rejected")):
            EndpointUpdate.send_update(ENDPOINTS)
