"""
``ExceptionWriter`` reports unhandled application errors. It runs inside an
already-failing request, so the one thing it must never do is raise — a
reporting failure that masks the original exception costs the actual diagnosis.
"""

from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.configuration import Configuration, LogMode
from end_point_blank.request_store import RequestStore
from end_point_blank.writers.exception_writer import ExceptionWriter


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


def raised(message="boom", error_class=ValueError):
    try:
        raise error_class(message)
    except error_class as exc:
        return exc


def write_and_capture(exc):
    with patch("end_point_blank.writers.exception_writer.DirectWriter") as writer_cls:
        writer = MagicMock()
        writer_cls.return_value = writer
        ExceptionWriter.write(exc)
    return writer.write.call_args[0][0][0]


class TestThePayload:
    def test_carries_the_exception_message(self):
        assert write_and_capture(raised("database timeout"))["message"] == "database timeout"

    def test_carries_the_stacktrace_as_a_list_of_lines(self):
        # Intake hashes the stacktrace array to group errors; a joined string
        # hashes to a different value and breaks grouping across languages.
        stacktrace = write_and_capture(raised())["stacktrace"]

        assert isinstance(stacktrace, list)
        assert all("\n" not in line for line in stacktrace)

    def test_carries_the_configured_app_name(self, _reset):
        _reset.app_name = "students-api"

        assert write_and_capture(raised())["app_name"] == "students-api"

    def test_stamps_a_sent_at_timestamp(self):
        assert write_and_capture(raised())["sent_at"].endswith("+00:00")

    def test_correlates_with_the_request_that_produced_it(self):
        # The request, response and error rows are joined on this uuid; without
        # it an error cannot be traced back to the call that caused it.
        RequestStore.set({"HTTP_X_REQUEST_ID": "req-abc"})

        assert write_and_capture(raised())["uuid"] == "req-abc"

    def test_is_given_a_uuid_even_without_a_request_id_header(self):
        RequestStore.set({})

        assert write_and_capture(raised())["uuid"] is not None

    def test_stamps_the_route_the_error_happened_on(self):
        RequestStore.set({"PATH_INFO": "/students/5", "REQUEST_METHOD": "POST"})

        payload = write_and_capture(raised())

        assert payload["stamped_path"] == "/students/5"
        assert payload["stamped_http_method"] == "POST"

    def test_reports_an_error_raised_outside_any_request(self):
        payload = write_and_capture(raised())

        assert payload["stamped_path"] is None
        assert payload["uuid"] is None

    def test_carries_the_source_application_environment(self):
        RequestStore.set({})
        RequestStore.set_source_application_environment_id("env-77")

        assert write_and_capture(raised())["source_application_environment_id"] == "env-77"


class TestMasking:
    def test_configured_rules_are_applied_to_the_message(self, _reset):
        # Exception messages routinely interpolate the value that broke — an
        # unmasked one is the most common way secrets reach the log store.
        _reset.masking_rules = [
            {"target": "error_message", "regex": r"\d{3}-\d{2}-\d{4}", "replacement_value": "[redacted]"}
        ]

        payload = write_and_capture(raised("invalid ssn 123-45-6789"))

        assert payload["message"] == "invalid ssn [redacted]"

    def test_the_mask_hook_runs_last(self, _reset):
        _reset.mask_hook = lambda payload, record_type: {**payload, "record_type": record_type}

        assert write_and_capture(raised())["record_type"] == "error"


class TestWriterSelection:
    def test_sends_directly_by_default(self):
        with patch("end_point_blank.writers.exception_writer.DirectWriter") as direct:
            ExceptionWriter.write(raised())

        direct.assert_called_once_with("application_errors_url")

    def test_queues_in_delayed_mode(self, _reset):
        _reset.log_mode = LogMode.DELAYED

        with patch("end_point_blank.writers.exception_writer.DelayedWriter") as delayed:
            ExceptionWriter.write(raised())

        delayed.assert_called_once_with("application_errors_url")


class TestFailureIsSwallowed:
    def test_a_transport_failure_does_not_mask_the_original_error(self):
        with patch("end_point_blank.writers.exception_writer.DirectWriter", side_effect=RuntimeError("no route")):
            ExceptionWriter.write(raised())

    def test_an_exception_with_no_traceback_is_still_reported(self):
        payload = write_and_capture(ValueError("never raised"))

        assert payload["message"] == "never raised"
        assert payload["stacktrace"] == ["ValueError: never raised"]
