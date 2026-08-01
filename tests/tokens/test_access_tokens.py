"""
``AccessTokens`` is the credential cache every authorize call goes through. A
token served past its life produces a 401 storm; a token thrown away too eagerly
produces a token-generation storm. Both failure modes are invisible in
development and expensive in production, so the expiry arithmetic is covered
here in detail.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from end_point_blank.tokens.access_tokens import AccessTokens

GENERATOR = "end_point_blank.commands.generate_access_token.GenerateAccessToken.token"


def payload(token="tok-1", expires_in_seconds=3600, **overrides):
    expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in_seconds)
    body = {"token": token, "expired_at": expiry.isoformat().replace("+00:00", "Z")}
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _clean():
    AccessTokens().clear()
    yield
    AccessTokens().clear()


class TestFetchingAToken:
    def test_returns_the_generated_token(self):
        with patch(GENERATOR, return_value=payload("tok-abc")):
            assert AccessTokens().token("api.example.com") == "tok-abc"

    def test_serves_a_later_call_from_the_cache(self):
        with patch(GENERATOR, return_value=payload("tok-abc")) as generate:
            AccessTokens().token("api.example.com")
            AccessTokens().token("api.example.com")

        assert generate.call_count == 1

    def test_treats_hostnames_case_insensitively(self):
        # The hostname reaches us from the Host header, whose case is the
        # caller's choice. Keying on it verbatim would mint a second token for
        # every casing variant a client happens to send.
        with patch(GENERATOR, return_value=payload()) as generate:
            AccessTokens().token("API.Example.COM")
            AccessTokens().token("api.example.com")

        assert generate.call_count == 1

    def test_keeps_a_token_with_more_than_the_refresh_buffer_left(self):
        with patch(GENERATOR, return_value=payload(expires_in_seconds=600)) as generate:
            AccessTokens().token("api.example.com")
            AccessTokens().token("api.example.com")

        assert generate.call_count == 1

    def test_regenerates_a_token_that_is_inside_the_refresh_buffer(self):
        # Refreshing two minutes early is what stops a token expiring mid-flight
        # on a slow request.
        responses = [payload("old", expires_in_seconds=60), payload("new")]

        with patch(GENERATOR, side_effect=responses) as generate:
            assert AccessTokens().token("api.example.com") == "old"
            assert AccessTokens().token("api.example.com") == "new"

        assert generate.call_count == 2

    def test_does_not_retain_a_token_that_arrives_already_expired(self):
        with patch(GENERATOR, return_value=payload(expires_in_seconds=-1)):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists("api.example.com") is False

    def test_keeps_hosts_separate(self):
        responses = [payload("tok-a"), payload("tok-b")]

        with patch(GENERATOR, side_effect=responses):
            assert AccessTokens().token("a.example.com") == "tok-a"
            assert AccessTokens().token("b.example.com") == "tok-b"


class TestWhenGenerationFails:
    def test_returns_none_when_the_generator_returns_nothing(self):
        with patch(GENERATOR, return_value=None):
            assert AccessTokens().token("api.example.com") is None

    def test_returns_none_when_the_payload_carries_an_error_instead_of_a_token(self):
        with patch(GENERATOR, return_value={"error": "invalid client"}):
            assert AccessTokens().token("api.example.com") is None

    def test_returns_none_when_the_token_field_is_blank(self):
        with patch(GENERATOR, return_value={"token": ""}):
            assert AccessTokens().token("api.example.com") is None

    def test_discards_the_stale_token_when_a_refresh_fails(self):
        # A failed refresh must not leave the expiring token behind claiming to
        # be usable — callers would keep presenting it right up to the 401.
        with patch(GENERATOR, return_value=payload(expires_in_seconds=60)):
            AccessTokens().token("api.example.com")

        with patch(GENERATOR, return_value=None):
            assert AccessTokens().token("api.example.com") is None

        assert AccessTokens().exists("api.example.com") is False

    def test_does_not_cache_the_failure(self):
        with patch(GENERATOR, return_value=None):
            AccessTokens().token("api.example.com")

        with patch(GENERATOR, return_value=payload("tok-recovered")) as generate:
            assert AccessTokens().token("api.example.com") == "tok-recovered"
            assert generate.call_count == 1


class TestExists:
    def test_is_false_for_a_host_never_seen(self):
        assert AccessTokens().exists("api.example.com") is False

    def test_is_true_for_a_freshly_generated_token(self):
        with patch(GENERATOR, return_value=payload()):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists("api.example.com") is True

    def test_is_false_when_less_than_thirty_seconds_remain(self):
        # ``Authorization.header`` treats "exists" as "safe to present", so a
        # token about to expire has to read as absent.
        with patch(GENERATOR, return_value=payload(expires_in_seconds=10)):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists("api.example.com") is False

    def test_matches_the_hostname_case_insensitively(self):
        with patch(GENERATOR, return_value=payload()):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists("API.EXAMPLE.COM") is True


class TestRemove:
    def test_drops_the_cached_token(self):
        with patch(GENERATOR, return_value=payload()):
            AccessTokens().token("api.example.com")

        AccessTokens().remove("api.example.com")

        assert AccessTokens().exists("api.example.com") is False

    def test_matches_the_hostname_case_insensitively(self):
        # ``EndpointAuthorize`` removes using the host it derived from the
        # request; if the casing had to match the fetch, the 401-retry would
        # re-present the same rejected token.
        with patch(GENERATOR, return_value=payload()):
            AccessTokens().token("api.example.com")

        AccessTokens().remove("API.EXAMPLE.COM")

        assert AccessTokens().exists("api.example.com") is False

    def test_is_silent_for_a_host_never_seen(self):
        AccessTokens().remove("nowhere.example.com")


class TestClear:
    def test_removes_every_cached_token(self):
        with patch(GENERATOR, side_effect=[payload("a"), payload("b")]):
            AccessTokens().token("a.example.com")
            AccessTokens().token("b.example.com")

        AccessTokens().clear()

        assert AccessTokens().exists("a.example.com") is False
        assert AccessTokens().exists("b.example.com") is False


class TestTheExpiryTimestamp:
    def test_accepts_the_z_suffixed_form_intake_sends(self):
        with patch(GENERATOR, return_value={"token": "t", "expired_at": "2099-01-01T00:00:00Z"}):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists("api.example.com") is True

    def test_accepts_an_explicit_utc_offset(self):
        with patch(GENERATOR, return_value={"token": "t", "expired_at": "2099-01-01T00:00:00+00:00"}):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists("api.example.com") is True

    @pytest.mark.parametrize(
        "expired_at", ["not-a-date", "", None, 1893456000], ids=["garbage", "blank", "null", "epoch-int"]
    )
    def test_falls_back_to_an_hour_when_the_expiry_is_unusable(self, expired_at):
        # Falling back to a short life keeps the token usable rather than
        # failing the request outright, and the next hour re-syncs with intake.
        with patch(GENERATOR, return_value={"token": "t", "expired_at": expired_at}):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists("api.example.com") is True

    def test_falls_back_to_an_hour_when_the_expiry_is_absent(self):
        with patch(GENERATOR, return_value={"token": "t"}):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists("api.example.com") is True


class TestTheSingleton:
    def test_every_construction_shares_one_cache(self):
        # Callers construct ``AccessTokens()`` fresh at each use site; if that
        # produced a new cache the token would be regenerated on every request.
        with patch(GENERATOR, return_value=payload()) as generate:
            AccessTokens().token("api.example.com")
            AccessTokens().token("api.example.com")

        assert generate.call_count == 1
        assert AccessTokens() is AccessTokens()
