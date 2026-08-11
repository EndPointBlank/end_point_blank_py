"""
``AccessTokens`` is the credential cache every authorize call goes through. A
token served past its life produces a 401 storm; a token thrown away too eagerly
produces a token-generation storm. Both failure modes are invisible in
development and expensive in production, so the expiry arithmetic is covered
here in detail.

Intake issues a token against the application environment the authenticating
credential belongs to, not against the hostname the request names. One process
authenticates as one application environment, so it holds one token and the
hostname is only ever part of the generation payload.
"""

import threading
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

    def test_serves_every_hostname_from_the_one_token(self):
        # The hostname reaches us from the Host header, so the caller chooses
        # it. Keying the cache on it meant a novel value cost a token exchange
        # and a database lookup on intake, for a token intake never scoped to
        # the hostname in the first place.
        with patch(GENERATOR, return_value=payload("tok-1")) as generate:
            assert AccessTokens().token("a.example.com") == "tok-1"
            assert AccessTokens().token("b.example.com") == "tok-1"
            assert AccessTokens().token("never.seen.example.com") == "tok-1"

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

        assert AccessTokens().exists() is False


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

        assert AccessTokens().exists() is False

    def test_leaves_a_live_token_alone_because_it_never_asks(self):
        # A live token is served without a generation call at all, so a hostname
        # intake would refuse cannot disturb it.
        with patch(GENERATOR, return_value=payload("tok-1")) as generate:
            AccessTokens().token("api.example.com")

            assert AccessTokens().token("bogus.example.com") == "tok-1"
            assert generate.call_count == 1

        assert AccessTokens().exists() is True

    def test_does_not_cache_the_failure(self):
        with patch(GENERATOR, return_value=None):
            AccessTokens().token("api.example.com")

        with patch(GENERATOR, return_value=payload("tok-recovered")) as generate:
            assert AccessTokens().token("api.example.com") == "tok-recovered"
            assert generate.call_count == 1


class TestExists:
    def test_is_false_before_any_token_is_issued(self):
        assert AccessTokens().exists() is False

    def test_is_true_for_a_freshly_generated_token(self):
        with patch(GENERATOR, return_value=payload()):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists() is True

    def test_is_false_when_less_than_thirty_seconds_remain(self):
        # ``Authorization.header`` treats "exists" as "safe to present", so a
        # token about to expire has to read as absent.
        with patch(GENERATOR, return_value=payload(expires_in_seconds=10)):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists() is False


class TestInvalidate:
    def test_drops_the_current_token(self):
        with patch(GENERATOR, return_value=payload()):
            current = AccessTokens().token("api.example.com")

        AccessTokens().invalidate(current)

        assert AccessTokens().exists() is False

    def test_ignores_a_token_that_has_already_been_replaced(self):
        # What stops a 401 from stampeding. Every request in flight when a token
        # is rejected reports the same stale value; only the first should cause
        # an exchange, because the rest are holding a token that has already
        # been replaced and clearing for them would discard a good one.
        with patch(GENERATOR, side_effect=[payload("tok-1"), payload("tok-2")]) as generate:
            stale = AccessTokens().token("api.example.com")
            AccessTokens().invalidate(stale)
            AccessTokens().token("api.example.com")

            AccessTokens().invalidate(stale)

            assert AccessTokens().token("api.example.com") == "tok-2"
            assert generate.call_count == 2

    def test_ignores_none(self):
        with patch(GENERATOR, return_value=payload("tok-1")) as generate:
            AccessTokens().token("api.example.com")

            AccessTokens().invalidate(None)

            assert AccessTokens().token("api.example.com") == "tok-1"
            assert generate.call_count == 1


class TestClear:
    def test_drops_the_cached_token(self):
        with patch(GENERATOR, return_value=payload("a")):
            AccessTokens().token("a.example.com")

        AccessTokens().clear()

        assert AccessTokens().exists() is False


class TestConcurrency:
    def test_callers_racing_for_a_token_share_one_generation(self):
        # The cached token is read without the lock; the lock is only taken to
        # exchange. A caller that waited for it has to re-read rather than
        # exchange again, or a burst at startup fans out into one call each.
        callers = 8
        start = threading.Barrier(callers)
        results = []
        results_lock = threading.Lock()

        def call():
            start.wait()
            value = AccessTokens().token("api.example.com")
            with results_lock:
                results.append(value)

        with patch(GENERATOR, return_value=payload("tok-1")) as generate:
            threads = [threading.Thread(target=call) for _ in range(callers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            assert generate.call_count == 1

        assert results == ["tok-1"] * callers


class TestTheExpiryTimestamp:
    def test_accepts_the_z_suffixed_form_intake_sends(self):
        with patch(GENERATOR, return_value={"token": "t", "expired_at": "2099-01-01T00:00:00Z"}):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists() is True

    def test_accepts_an_explicit_utc_offset(self):
        with patch(GENERATOR, return_value={"token": "t", "expired_at": "2099-01-01T00:00:00+00:00"}):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists() is True

    @pytest.mark.parametrize(
        "expired_at", ["not-a-date", "", None, 1893456000], ids=["garbage", "blank", "null", "epoch-int"]
    )
    def test_falls_back_to_an_hour_when_the_expiry_is_unusable(self, expired_at):
        # Falling back to a short life keeps the token usable rather than
        # failing the request outright, and the next hour re-syncs with intake.
        with patch(GENERATOR, return_value={"token": "t", "expired_at": expired_at}):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists() is True

    def test_falls_back_to_an_hour_when_the_expiry_is_absent(self):
        with patch(GENERATOR, return_value={"token": "t"}):
            AccessTokens().token("api.example.com")

        assert AccessTokens().exists() is True


class TestTheSingleton:
    def test_every_construction_shares_one_cache(self):
        # Callers construct ``AccessTokens()`` fresh at each use site; if that
        # produced a new cache the token would be regenerated on every request.
        with patch(GENERATOR, return_value=payload()) as generate:
            AccessTokens().token("api.example.com")
            AccessTokens().token("api.example.com")

        assert generate.call_count == 1
        assert AccessTokens() is AccessTokens()
