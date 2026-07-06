"""Tests hardening the shared HTTP helper (P2 #14): connect/read timeout split."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from end_point_blank.commands import _http


@pytest.fixture(autouse=True)
def reset_session():
    """Ensure each test gets a fresh per-thread session mock target."""
    if hasattr(_http._local, "session"):
        del _http._local.session
    yield
    if hasattr(_http._local, "session"):
        del _http._local.session


def test_timeout_constants_define_a_connect_read_split():
    assert _http._CONNECT_TIMEOUT == 3
    assert _http._READ_TIMEOUT == 5


def test_post_passes_connect_read_tuple_as_timeout():
    mock_session = MagicMock()
    mock_session.post.return_value = MagicMock(status_code=201)

    with patch.object(_http, "_session", return_value=mock_session):
        _http.post("https://intake.example/authorize", "Bearer abc", {"a": 1})

    mock_session.post.assert_called_once()
    _, kwargs = mock_session.post.call_args
    assert kwargs["timeout"] == (_http._CONNECT_TIMEOUT, _http._READ_TIMEOUT)
    assert kwargs["timeout"] == (3, 5)


def test_post_returns_none_and_logs_on_connect_timeout(caplog):
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.exceptions.ConnectTimeout("connect timed out")

    with patch.object(_http, "_session", return_value=mock_session):
        with patch("time.sleep"):
            result = _http.post("https://intake.example/authorize", "Bearer abc", {"a": 1})

    assert result is None
    assert mock_session.post.call_count == 3


def test_post_returns_none_and_logs_on_read_timeout(caplog):
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.exceptions.ReadTimeout("read timed out")

    with patch.object(_http, "_session", return_value=mock_session):
        with patch("time.sleep"):
            result = _http.post("https://intake.example/authorize", "Bearer abc", {"a": 1})

    assert result is None
    assert mock_session.post.call_count == 3


def test_post_returns_none_on_generic_timeout_without_raising():
    """A bare requests.exceptions.Timeout must be swallowed just like other RequestExceptions."""
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.exceptions.Timeout("timed out")

    with patch.object(_http, "_session", return_value=mock_session):
        with patch("time.sleep"):
            result = _http.post("https://intake.example/authorize", "Bearer abc", {"a": 1})

    assert result is None
