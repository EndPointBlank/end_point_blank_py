"""
``DirectWriter`` is the single point every payload leaves through. Its URL
resolution is the interesting part: requests and responses go to the log host
while authorization traffic goes to intake, and a payload posted to the wrong
one is accepted and silently filed as the wrong record type.
"""

from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.configuration import Configuration
from end_point_blank.writers import direct_writer as dw
from end_point_blank.writers.direct_writer import DirectWriter


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    for key in ("ENDPOINTBLANK_BASE_URL", "ENDPOINTBLANK_LOG_BASE_URL", "ENDPOINTBLANK_CLIENT_ID",
                "ENDPOINTBLANK_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    config.base_url = "https://intake.test"
    config.log_base_url = "https://log.test"
    yield config
    config._init_defaults()


def response(status=201, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


class TestUrlResolution:
    @pytest.mark.parametrize(
        "url_key, expected",
        [
            ("application_errors_url", "https://log.test/api/application_errors"),
            ("endpoint_error_url", "https://intake.test/api/endpoint_errors"),
            ("log_url", "https://log.test/api/application_logs"),
            ("requests_url", "https://log.test/api/application_requests"),
            ("responses_url", "https://log.test/api/application_responses"),
        ],
    )
    def test_each_key_resolves_to_its_own_endpoint(self, url_key, expected):
        with patch.object(dw, "post", return_value=response()) as post:
            DirectWriter(url_key).write([{"a": 1}])

        assert post.call_args[0][0] == expected

    def test_an_unknown_key_still_delivers_somewhere(self):
        # Dropping the payload on a typo'd key would lose telemetry silently;
        # the error endpoint is the visible place to land it.
        with patch.object(dw, "post", return_value=response()) as post:
            DirectWriter("nonsense_url").write([{"a": 1}])

        assert post.call_args[0][0] == "https://log.test/api/application_errors"

    def test_the_url_follows_configuration_at_construction(self, _config):
        _config.log_base_url = "https://elsewhere.test"

        with patch.object(dw, "post", return_value=response()) as post:
            DirectWriter("log_url").write([{"a": 1}])

        assert post.call_args[0][0] == "https://elsewhere.test/api/application_logs"


class TestTheRequest:
    def test_wraps_the_batch_under_a_payload_key(self):
        # Intake reads a batch off "payload"; sending the bare list is a 422.
        batch = [{"a": 1}, {"b": 2}]

        with patch.object(dw, "post", return_value=response()) as post:
            DirectWriter("log_url").write(batch)

        assert post.call_args[0][2] == {"payload": batch}

    def test_authenticates_the_request(self, _config):
        _config.client_id = "cid"
        _config.client_secret = "secret"

        with patch.object(dw, "post", return_value=response()) as post:
            DirectWriter("log_url").write([{"a": 1}])

        assert post.call_args[0][1].startswith("Basic ")

    def test_sends_an_empty_batch_without_complaint(self):
        with patch.object(dw, "post", return_value=response()) as post:
            DirectWriter("log_url").write([])

        assert post.call_args[0][2] == {"payload": []}


class TestFailureIsNotFatal:
    def test_an_unreachable_endpoint_does_not_raise(self):
        # Writers are called from the middleware's finally block; raising here
        # would replace the application's real response with a 500.
        with patch.object(dw, "post", return_value=None):
            DirectWriter("log_url").write([{"a": 1}])

    @pytest.mark.parametrize("status", [400, 422, 500])
    def test_a_rejected_batch_does_not_raise(self, status):
        with patch.object(dw, "post", return_value=response(status, text="rejected")):
            DirectWriter("log_url").write([{"a": 1}])
