"""
``EndpointAuthorize`` is the SDK's highest-risk module and, until now, its least
tested. It does credential exchange, retry-on-401, caching, and it decides
whether a request is allowed to proceed — and both bugs found during the RFC
header work lived here.

These cover the whole surface: the cache (hit, miss, key composition), the
token-retry path, every response class, and the deprecation extraction.
"""

from unittest.mock import MagicMock, patch

import pytest

from end_point_blank.commands import endpoint_authorize as ea
from end_point_blank.commands.authentication_cache import AuthenticationCache
from end_point_blank.commands.endpoint_authorize import EndpointAuthorize, _CachedResponse
from end_point_blank.request_store import RequestStore


def environ(**overrides):
    base = {
        "HTTP_AUTHORIZATION": "Basic Y2xpZW50",
        "REQUEST_METHOD": "GET",
        "HTTP_HOST": "localhost:3002",
        "REMOTE_ADDR": "10.0.0.1",
    }
    base.update(overrides)
    return base


def response(status=201, payload=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = payload if payload is not None else {"authorized": True}
    return resp


@pytest.fixture(autouse=True)
def _clean():
    AuthenticationCache().clear()
    RequestStore.set({})
    yield
    AuthenticationCache().clear()
    RequestStore.clear()


@pytest.fixture(autouse=True)
def _auth_header():
    with patch.object(ea.Authorization, "header", return_value="Basic c2VydmVy"):
        yield


class TestTheAuthorizedPath:
    def test_returns_the_response_and_caches_it(self):
        with patch.object(ea, "post", return_value=response()) as post:
            result = EndpointAuthorize.authorize(environ(), "/students", "1")

            assert result.status_code == 201
            assert post.call_count == 1

        # Second call is served from cache — no second HTTP request.
        with patch.object(ea, "post") as post:
            again = EndpointAuthorize.authorize(environ(), "/students", "1")

            assert isinstance(again, _CachedResponse)
            assert again.status_code == 201
            post.assert_not_called()

    def test_sends_the_request_details_intake_needs(self):
        with patch.object(ea, "post", return_value=response()) as post:
            EndpointAuthorize.authorize(environ(), "/students", "1")

        _url, _auth, body = post.call_args[0]

        assert body["path"] == "/students"
        assert body["http_method"] == "GET"
        assert body["endpoint_version"] == "1"
        assert body["client_auth"] == "Basic Y2xpZW50"
        # HTTP_HOST carries the port; intake matches on hostname alone.
        assert body["target_hostname"] == "localhost"

    def test_prefers_the_forwarded_ip_over_the_socket_address(self):
        env = environ(HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")

        with patch.object(ea, "post", return_value=response()) as post:
            EndpointAuthorize.authorize(env, "/students", "1")

        assert post.call_args[0][2]["source_ip"] == "203.0.113.7"

    def test_falls_back_to_remote_addr(self):
        with patch.object(ea, "post", return_value=response()) as post:
            EndpointAuthorize.authorize(environ(), "/students", "1")

        assert post.call_args[0][2]["source_ip"] == "10.0.0.1"

    def test_uses_server_name_when_there_is_no_host_header(self):
        env = environ(SERVER_NAME="api.example.com")
        env.pop("HTTP_HOST")

        with patch.object(ea, "post", return_value=response()) as post:
            EndpointAuthorize.authorize(env, "/students", "1")

        assert post.call_args[0][2]["target_hostname"] == "api.example.com"


class TestTheCacheKey:
    """Regression cover for the bug the end-to-end run found: the key must vary
    with the endpoint version, or two callers on different versions of the same
    route share one answer."""

    def test_a_different_version_is_a_different_entry(self):
        deprecated = response(payload={"deprecation": {"deprecated_at": "2026-01-01T00:00:00Z"}})

        with patch.object(ea, "post", return_value=deprecated):
            EndpointAuthorize.authorize(environ(), "/students", "1")
        assert RequestStore.get_deprecation() == {"deprecated_at": "2026-01-01T00:00:00Z"}

        # Same client, path and method — only the version differs.
        with patch.object(ea, "post", return_value=response()) as post:
            EndpointAuthorize.authorize(environ(), "/students", "2")
            assert post.call_count == 1, "v2 must authorize for real, not reuse v1's entry"

        assert RequestStore.get_deprecation() is None

    def test_a_different_client_is_a_different_entry(self):
        with patch.object(ea, "post", return_value=response()):
            EndpointAuthorize.authorize(environ(), "/students", "1")

        with patch.object(ea, "post", return_value=response()) as post:
            EndpointAuthorize.authorize(environ(HTTP_AUTHORIZATION="Basic b3RoZXI="), "/students", "1")
            assert post.call_count == 1

    def test_a_different_method_is_a_different_entry(self):
        with patch.object(ea, "post", return_value=response()):
            EndpointAuthorize.authorize(environ(), "/students", "1")

        with patch.object(ea, "post", return_value=response()) as post:
            EndpointAuthorize.authorize(environ(REQUEST_METHOD="POST"), "/students", "1")
            assert post.call_count == 1


class TestDeprecation:
    def test_stores_the_block_when_present(self):
        block = {"deprecated_at": "2026-01-01T00:00:00Z", "sunset_at": "2026-11-11T11:11:11Z"}

        with patch.object(ea, "post", return_value=response(payload={"deprecation": block})):
            EndpointAuthorize.authorize(environ(), "/students", "1")

        assert RequestStore.get_deprecation() == block

    def test_stores_none_when_the_version_is_not_deprecated(self):
        with patch.object(ea, "post", return_value=response()):
            EndpointAuthorize.authorize(environ(), "/students", "1")

        assert RequestStore.get_deprecation() is None

    def test_survives_a_cache_hit(self):
        # Without this the headers would appear only on cache misses — roughly
        # one request in N, which reads as flaky rather than missing.
        block = {"deprecated_at": "2026-01-01T00:00:00Z"}

        with patch.object(ea, "post", return_value=response(payload={"deprecation": block})):
            EndpointAuthorize.authorize(environ(), "/students", "1")

        RequestStore.set({})  # a fresh request

        with patch.object(ea, "post") as post:
            EndpointAuthorize.authorize(environ(), "/students", "1")
            post.assert_not_called()

        assert RequestStore.get_deprecation() == block

    @pytest.mark.parametrize(
        "payload", [{"deprecation": None}, {"deprecation": "nonsense"}, {"deprecation": []}, {}, None]
    )
    def test_ignores_a_block_that_is_not_a_mapping(self, payload):
        with patch.object(ea, "post", return_value=response(payload=payload)):
            EndpointAuthorize.authorize(environ(), "/students", "1")

        assert RequestStore.get_deprecation() is None

    def test_ignores_a_body_that_is_not_json(self):
        resp = response()
        resp.json.side_effect = ValueError("not json")

        with patch.object(ea, "post", return_value=resp):
            EndpointAuthorize.authorize(environ(), "/students", "1")

        assert RequestStore.get_deprecation() is None


class TestTheTokenRetry:
    def test_a_401_on_a_bearer_token_clears_it_and_retries_once(self):
        with patch.object(ea.Authorization, "header", return_value="Bearer stale-token"):
            with patch.object(ea.AccessTokens, "remove") as remove:
                with patch.object(ea, "post", side_effect=[response(401), response(201)]) as post:
                    result = EndpointAuthorize.authorize(environ(), "/students", "1")

        assert result.status_code == 201
        assert post.call_count == 2
        remove.assert_called_once_with("localhost")

    def test_a_401_on_basic_auth_is_not_retried(self):
        # Basic credentials do not go stale the way a token does; retrying would
        # be a second identical request that fails identically.
        with patch.object(ea, "post", return_value=response(401)) as post:
            result = EndpointAuthorize.authorize(environ(), "/students", "1")

        assert result.status_code == 401
        assert post.call_count == 1

    def test_a_retry_that_also_fails_returns_the_failure(self):
        with patch.object(ea.Authorization, "header", return_value="Bearer stale-token"):
            with patch.object(ea.AccessTokens, "remove"):
                with patch.object(ea, "post", side_effect=[response(401), response(401)]):
                    result = EndpointAuthorize.authorize(environ(), "/students", "1")

        assert result.status_code == 401


class TestFailureResponses:
    def test_returns_none_when_intake_is_unreachable(self):
        with patch.object(ea, "post", return_value=None):
            assert EndpointAuthorize.authorize(environ(), "/students", "1") is None

    def test_returns_none_when_the_retry_also_yields_nothing(self):
        with patch.object(ea.Authorization, "header", return_value="Bearer stale"):
            with patch.object(ea.AccessTokens, "remove"):
                with patch.object(ea, "post", side_effect=[response(401), None]):
                    assert EndpointAuthorize.authorize(environ(), "/students", "1") is None

    @pytest.mark.parametrize("status", [400, 401, 403, 500])
    def test_a_failure_is_returned_and_not_cached(self, status):
        with patch.object(ea, "post", return_value=response(status, text="denied")) as post:
            first = EndpointAuthorize.authorize(environ(), "/students", "1")
            assert first.status_code == status

        # A denial must not be cached, or a later grant would not take effect.
        with patch.object(ea, "post", return_value=response(201)) as post:
            EndpointAuthorize.authorize(environ(), "/students", "1")
            assert post.call_count == 1
