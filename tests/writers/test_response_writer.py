"""
``ResponseWriter`` runs from the middleware's ``finally`` block, so it sees both
successful and failed requests. Its body handling is the part with a budget:
response bodies are unbounded and the payload is not.

The request/method correlation contract is covered in ``tests/test_response_writer.py``.
"""

from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.configuration import Configuration, LogMode
from end_point_blank.request_store import RequestStore
from end_point_blank.writers import response_writer as rw
from end_point_blank.writers.response_writer import ResponseWriter


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for key in ("ENDPOINTBLANK_APP_NAME", "ENDPOINTBLANK_ENV", "ENDPOINTBLANK_LOG_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    RequestStore.clear()
    rw._delayed_writer = None
    yield config
    rw._delayed_writer = None
    RequestStore.clear()
    config._init_defaults()


def write_and_capture(**kwargs):
    kwargs.setdefault("status", 200)
    with patch("end_point_blank.writers.response_writer.DirectWriter") as writer_cls:
        writer = MagicMock()
        writer_cls.return_value = writer
        ResponseWriter.write(**kwargs)
    return writer.write.call_args[0][0][0]


class TestThePayload:
    def test_carries_the_status(self):
        assert write_and_capture(status=201)["status"] == 201

    def test_carries_a_null_status_when_the_response_never_formed(self):
        # The middleware's finally block runs even when the app raised before
        # start_response; intake needs the row regardless.
        assert write_and_capture(status=None)["status"] is None

    def test_carries_the_headers(self):
        assert write_and_capture(headers={"X-Custom": "yes"})["headers"] == {"X-Custom": "yes"}

    def test_defaults_the_headers_to_an_empty_map(self):
        assert write_and_capture()["headers"] == {}

    def test_defaults_the_extra_data_to_an_empty_map(self):
        assert write_and_capture()["data"] == {}

    def test_carries_supplied_extra_data(self):
        assert write_and_capture(data={"duration_ms": 12})["data"] == {"duration_ms": 12}

    def test_carries_the_configured_app_name_and_environment(self, _reset):
        _reset.app_name = "students-api"
        _reset.environment = "production"

        payload = write_and_capture()

        assert payload["app_name"] == "students-api"
        assert payload["env"] == "production"

    def test_correlates_with_the_other_rows_for_this_request(self):
        RequestStore.set({"HTTP_X_REQUEST_ID": "req-abc"})

        assert write_and_capture()["uuid"] == "req-abc"

    def test_carries_the_source_application_environment(self):
        RequestStore.set({})
        RequestStore.set_source_application_environment_id("env-77")

        assert write_and_capture()["source_application_environment_id"] == "env-77"


class TestTheBody:
    def test_records_a_short_body_verbatim(self):
        assert write_and_capture(body="ok")["body"] == "ok"

    def test_records_no_body_as_null(self):
        assert write_and_capture(body=None)["body"] is None

    def test_leaves_a_body_at_the_limit_alone(self):
        assert write_and_capture(body="x" * 1024)["body"] == "x" * 1024

    def test_shortens_an_oversized_body(self):
        # An unbounded response body would take the whole batch over intake's
        # payload limit, losing the other rows sent alongside it.
        recorded = write_and_capture(body="x" * 5000)["body"]

        assert recorded.endswith("...")
        assert len(recorded) == 1027


class TestMasking:
    def test_configured_rules_are_applied_to_the_body(self, _reset):
        _reset.masking_rules = [
            {"target": "response_body", "path": "$.token", "replacement_value": "[redacted]"}
        ]

        assert '"token": "[redacted]"' in write_and_capture(body='{"token":"abc123"}')["body"]


class TestWriterSelection:
    def test_sends_directly_by_default(self):
        with patch("end_point_blank.writers.response_writer.DirectWriter") as direct:
            ResponseWriter.write(status=200)

        direct.assert_called_once_with("responses_url")

    def test_reuses_one_queue_in_delayed_mode(self, _reset):
        # A DelayedWriter spawns worker threads on construction, so one per
        # response would leak four threads per request.
        _reset.log_mode = LogMode.DELAYED

        with patch("end_point_blank.writers.response_writer.DelayedWriter") as delayed:
            ResponseWriter.write(status=200)
            ResponseWriter.write(status=200)

        assert delayed.call_count == 1


class TestFailureIsSwallowed:
    def test_a_transport_failure_does_not_reach_the_application(self):
        # This runs in the middleware's finally block; raising here would replace
        # the application's real response with a 500.
        with patch("end_point_blank.writers.response_writer.DirectWriter", side_effect=RuntimeError("no route")):
            ResponseWriter.write(status=200)
