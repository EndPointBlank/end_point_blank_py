"""
Contract: intake now matches spy health checks to an endpoint by route path
+ HTTP method, so ResponseWriter must send the request's HTTP method
alongside route. This locks in that the payload carries `method` sourced
from the WSGI environ's REQUEST_METHOD (uppercase verb), and that it is
None when there is no environ (e.g. request store was never populated).
"""

from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.configuration import Configuration
from end_point_blank.request_store import RequestStore
from end_point_blank.writers.response_writer import ResponseWriter


def make_environ(method="GET", path="/api/v1/test"):
    return {"REQUEST_METHOD": method, "PATH_INFO": path}


@pytest.fixture(autouse=True)
def reset_state():
    config = Configuration()
    config._init_defaults()
    RequestStore.clear()
    yield
    RequestStore.clear()
    config._init_defaults()


def _write_and_capture(status=200):
    with patch("end_point_blank.writers.response_writer.DirectWriter") as mock_cls:
        mock_writer = MagicMock()
        mock_cls.return_value = mock_writer
        ResponseWriter.write(status=status)
    return mock_writer.write.call_args[0][0][0]


def test_write_includes_method_from_environ():
    RequestStore.set(make_environ(method="POST"))

    payload = _write_and_capture()

    assert "route" in payload
    assert payload["method"] == "POST"


def test_write_method_is_none_without_environ():
    RequestStore.clear()

    payload = _write_and_capture()

    assert payload["method"] is None
