"""
``LogWriter`` is the one writer applications call directly. It must never raise:
a logging call that throws would turn an incidental telemetry failure into an
application outage.
"""

from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.configuration import Configuration, LogMode
from end_point_blank.request_store import RequestStore
from end_point_blank.writers.log_writer import LogWriter


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for key in ("ENDPOINTBLANK_APP_NAME", "ENDPOINTBLANK_LOG_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    config = Configuration()
    config._init_defaults()
    RequestStore.clear()
    yield config
    RequestStore.clear()
    config._init_defaults()


def write_and_capture(call, *args, **kwargs):
    with patch("end_point_blank.writers.log_writer.DirectWriter") as writer_cls:
        writer = MagicMock()
        writer_cls.return_value = writer
        call(*args, **kwargs)
    return writer.write.call_args[0][0][0]


class TestTheLevelHelpers:
    @pytest.mark.parametrize(
        "method, level",
        [(LogWriter.info, "info"), (LogWriter.warn, "warn"), (LogWriter.error, "error"), (LogWriter.fatal, "fatal")],
    )
    def test_each_helper_stamps_its_own_level(self, method, level):
        assert write_and_capture(method, "something happened")["log_level"] == level

    def test_the_data_argument_is_optional(self):
        assert write_and_capture(LogWriter.info, "no data")["data"] == {}

    def test_carries_the_supplied_data(self):
        payload = write_and_capture(LogWriter.info, "paid", {"amount": 42})

        assert payload["data"] == {"amount": 42}


class TestThePayload:
    def test_carries_the_message(self):
        assert write_and_capture(LogWriter.write, "disk full", "error")["message"] == "disk full"

    def test_carries_the_configured_app_name(self, _reset):
        _reset.app_name = "students-api"

        assert write_and_capture(LogWriter.info, "hi")["app_name"] == "students-api"

    def test_stamps_a_sent_at_timestamp(self):
        assert write_and_capture(LogWriter.info, "hi")["sent_at"].endswith("+00:00")

    def test_stamps_the_route_of_the_request_it_was_logged_from(self):
        # Log rows are joined to endpoints in the portal on path + method; without
        # the stamp a log line cannot be attributed to the route that produced it.
        RequestStore.set({"PATH_INFO": "/students/5", "REQUEST_METHOD": "POST"})

        payload = write_and_capture(LogWriter.info, "hi")

        assert payload["stamped_path"] == "/students/5"
        assert payload["stamped_http_method"] == "POST"

    def test_leaves_the_route_unstamped_outside_a_request(self):
        payload = write_and_capture(LogWriter.info, "startup complete")

        assert payload["stamped_path"] is None
        assert payload["stamped_http_method"] is None

    def test_carries_the_request_id_supplied_by_the_caller(self):
        RequestStore.set({"HTTP_X_REQUEST_ID": "req-abc"})

        assert write_and_capture(LogWriter.info, "hi")["uuid"] == "req-abc"

    def test_carries_the_source_application_environment(self):
        RequestStore.set({})
        RequestStore.set_source_application_environment_id("env-77")

        assert write_and_capture(LogWriter.info, "hi")["source_application_environment_id"] == "env-77"


class TestWriterSelection:
    def test_sends_directly_by_default(self):
        with patch("end_point_blank.writers.log_writer.DirectWriter") as direct:
            LogWriter.info("hi")

        direct.assert_called_once_with("log_url")

    def test_queues_in_delayed_mode(self, _reset):
        _reset.log_mode = LogMode.DELAYED

        with patch("end_point_blank.writers.log_writer.DelayedWriter") as delayed:
            LogWriter.info("hi")

        delayed.assert_called_once_with("log_url")


class TestFailureIsSwallowed:
    def test_a_transport_failure_does_not_reach_the_caller(self):
        # An application calls LogWriter.info in its own request path. If the
        # telemetry transport can raise, a broken intake takes the app with it.
        with patch("end_point_blank.writers.log_writer.DirectWriter", side_effect=RuntimeError("no route to host")):
            LogWriter.info("hi")

    def test_an_unserialisable_message_does_not_reach_the_caller(self):
        class Explodes:
            def __str__(self):
                raise ValueError("boom")

        with patch("end_point_blank.writers.log_writer.DirectWriter") as writer_cls:
            writer_cls.return_value.write.side_effect = ValueError("boom")
            LogWriter.info(Explodes())
