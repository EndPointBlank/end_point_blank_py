"""
``RequestWriter`` records the inbound request. It is the only writer that reads
the request body, which means it is the only one that can break the application
it is observing — draining ``wsgi.input`` without putting it back leaves the view
with an empty body.
"""

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.configuration import Configuration, LogMode
from end_point_blank.request_store import RequestStore
from end_point_blank.writers import request_writer as rw
from end_point_blank.writers.request_writer import RequestWriter


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


def environ(body=b"", **overrides):
    base = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/students",
        "HTTP_HOST": "api.example.com",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": BytesIO(body),
    }
    base.update(overrides)
    return base


def write_and_capture():
    with patch("end_point_blank.writers.request_writer.DirectWriter") as writer_cls:
        writer = MagicMock()
        writer_cls.return_value = writer
        RequestWriter.write()
    return writer.write.call_args[0][0][0]


class TestOutsideARequest:
    def test_writes_nothing_when_no_request_is_in_flight(self):
        with patch("end_point_blank.writers.request_writer.DirectWriter") as writer_cls:
            RequestWriter.write()

        writer_cls.assert_not_called()


class TestThePayload:
    def test_carries_the_path_and_method(self):
        RequestStore.set(environ())

        payload = write_and_capture()

        assert payload["path"] == "/students"
        assert payload["http_method"] == "POST"

    def test_carries_the_configured_app_name_and_environment(self, _reset):
        _reset.app_name = "students-api"
        _reset.environment = "production"
        RequestStore.set(environ())

        payload = write_and_capture()

        assert payload["app_name"] == "students-api"
        assert payload["env"] == "production"

    def test_prefers_the_host_header_over_the_server_name(self):
        RequestStore.set(environ(SERVER_NAME="internal.local"))

        assert write_and_capture()["host"] == "api.example.com"

    def test_falls_back_to_the_server_name(self):
        env = environ(SERVER_NAME="internal.local")
        env.pop("HTTP_HOST")
        RequestStore.set(env)

        assert write_and_capture()["host"] == "internal.local"

    def test_carries_the_detected_endpoint_version(self):
        RequestStore.set(environ(HTTP_X_API_VERSION="v2"))

        assert write_and_capture()["endpoint_version"] == "2"

    def test_correlates_with_the_other_rows_for_this_request(self):
        RequestStore.set(environ(HTTP_X_REQUEST_ID="req-abc"))

        assert write_and_capture()["uuid"] == "req-abc"

    def test_stamps_a_sent_at_timestamp(self):
        RequestStore.set(environ())

        assert write_and_capture()["sent_at"].endswith("+00:00")

    def test_splits_the_port_off_the_host_header(self):
        # HTTP_HOST carries the port; this client used to ship
        # "api.example.com:8443" as the host while the other four shipped
        # "api.example.com" for the same request.
        RequestStore.set(environ(HTTP_HOST="API.Example.com:8443", **{"wsgi.url_scheme": "https"}))

        payload = write_and_capture()

        assert payload["host"] == "api.example.com"
        assert payload["scheme"] == "https"
        assert payload["port"] == 8443

    def test_omits_the_base_url_fields_it_cannot_resolve(self):
        env = environ()
        env.pop("HTTP_HOST")
        RequestStore.set(env)

        payload = write_and_capture()

        assert "host" not in payload
        assert "scheme" not in payload
        assert "port" not in payload

    # The pair below is the wiring check for the flag: same proxied request,
    # both settings. If the writer ever stops passing the configured value
    # through, the second test reports api.example.com and fails.
    @staticmethod
    def _proxied_environ():
        return environ(
            **{
                "wsgi.url_scheme": "http",
                "SERVER_PORT": "8080",
                "HTTP_HOST": "internal.svc:8080",
                "HTTP_X_FORWARDED_PROTO": "https",
                "HTTP_X_FORWARDED_HOST": "api.example.com",
                "HTTP_X_FORWARDED_PORT": "443",
            }
        )

    def test_reports_the_forwarded_values_when_proxy_headers_are_trusted(self):
        Configuration().trust_proxy_headers = True
        RequestStore.set(self._proxied_environ())

        payload = write_and_capture()

        assert payload["scheme"] == "https"
        assert payload["host"] == "api.example.com"
        assert "port" not in payload

    def test_reports_the_connection_and_host_header_when_proxy_headers_are_not_trusted(self):
        Configuration().trust_proxy_headers = False
        RequestStore.set(self._proxied_environ())

        payload = write_and_capture()

        assert payload["scheme"] == "http"
        assert payload["host"] == "internal.svc"
        assert payload["port"] == 8080


class TestTheHeaders:
    def test_converts_wsgi_keys_back_to_header_names(self):
        RequestStore.set(environ(HTTP_X_REQUEST_ID="req-abc"))

        assert write_and_capture()["headers"]["X-Request-Id"] == "req-abc"

    def test_includes_the_content_headers_wsgi_keeps_unprefixed(self):
        # CONTENT_TYPE and CONTENT_LENGTH have no HTTP_ prefix in a WSGI environ;
        # a naive prefix scan drops them and the recorded request loses its type.
        RequestStore.set(environ(b'{"a":1}'))

        headers = write_and_capture()["headers"]

        assert headers["Content-Type"] == "application/json"
        assert headers["Content-Length"] == "7"

    def test_ignores_non_header_environ_keys(self):
        RequestStore.set(environ(SERVER_PROTOCOL="HTTP/1.1"))

        assert "Server-Protocol" not in write_and_capture()["headers"]


class TestTheBody:
    def test_records_the_request_body(self):
        RequestStore.set(environ(b'{"name":"ada"}'))

        assert write_and_capture()["request"] == '{"name":"ada"}'

    def test_leaves_the_body_readable_for_the_application(self):
        # This is the regression that matters: reading wsgi.input consumes it, so
        # the stream has to be replaced or every POST handler sees an empty body.
        env = environ(b'{"name":"ada"}')
        RequestStore.set(env)

        write_and_capture()

        assert env["wsgi.input"].read() == b'{"name":"ada"}'

    def test_records_no_body_for_a_request_without_one(self):
        RequestStore.set(environ())

        assert write_and_capture()["request"] is None

    def test_records_no_body_when_the_length_is_missing(self):
        RequestStore.set(environ(b'{"name":"ada"}', CONTENT_LENGTH=""))

        assert write_and_capture()["request"] is None

    def test_records_no_body_when_there_is_no_input_stream(self):
        env = environ()
        env.pop("wsgi.input")
        RequestStore.set(env)

        assert write_and_capture()["request"] is None

    def test_an_unreadable_stream_does_not_fail_the_request(self):
        stream = MagicMock()
        stream.read.side_effect = OSError("connection reset")
        RequestStore.set(environ(b"xxx", **{"wsgi.input": stream}))

        assert write_and_capture()["request"] is None

    def test_undecodable_bytes_are_recorded_rather_than_dropped(self):
        RequestStore.set(environ(b"\xff\xfe binary"))

        assert write_and_capture()["request"] is not None


class TestMasking:
    def test_configured_rules_are_applied_to_the_body(self, _reset):
        _reset.masking_rules = [
            {"target": "request_body", "path": "$.password", "replacement_value": "[redacted]"}
        ]
        RequestStore.set(environ(b'{"password":"hunter2"}'))

        assert '"password": "[redacted]"' in write_and_capture()["request"]

    def test_configured_rules_are_applied_to_the_headers(self, _reset):
        _reset.masking_rules = [
            {"target": "request_headers", "path": "$.Authorization", "replacement_value": "[redacted]"}
        ]
        RequestStore.set(environ(HTTP_AUTHORIZATION="Basic c2VjcmV0"))

        assert write_and_capture()["headers"]["Authorization"] == "[redacted]"


class TestWriterSelection:
    def test_sends_directly_by_default(self):
        RequestStore.set(environ())

        with patch("end_point_blank.writers.request_writer.DirectWriter") as direct:
            RequestWriter.write()

        direct.assert_called_once_with("requests_url")

    def test_reuses_one_queue_in_delayed_mode(self, _reset):
        # A DelayedWriter spawns worker threads on construction. Building one per
        # request would leak four threads per request until the process dies.
        _reset.log_mode = LogMode.DELAYED
        RequestStore.set(environ())

        with patch("end_point_blank.writers.request_writer.DelayedWriter") as delayed:
            RequestWriter.write()
            RequestWriter.write()

        assert delayed.call_count == 1


class TestFailureIsSwallowed:
    def test_a_transport_failure_does_not_reach_the_application(self):
        RequestStore.set(environ())

        with patch("end_point_blank.writers.request_writer.DirectWriter", side_effect=RuntimeError("no route")):
            RequestWriter.write()
