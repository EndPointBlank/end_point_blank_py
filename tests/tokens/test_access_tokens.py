"""
``AccessTokens`` is the credential cache every outbound call goes through. A
token served past its life produces a 401 storm; a token thrown away too eagerly
produces a token-generation storm. Both failure modes are invisible in
development and expensive in production, so the expiry arithmetic is covered
here in detail.

A caller asks for the URL it is about to call. Intake answers with the canonical
base URL of the application environment that URL resolved to, and that returned
value -- not the URL the caller supplied -- is the cache key. A process that
calls several targets therefore holds several tokens.
"""

import sys
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import call, patch

import pytest

from end_point_blank.tokens.access_tokens import AccessTokens

GENERATOR = "end_point_blank.commands.generate_access_token.GenerateAccessToken.token"

BASE = "https://api.example.com/orders"


def payload(token="tok-1", expires_in_seconds=3600, **overrides):
    expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in_seconds)
    body = {
        "token": token,
        "expired_at": expiry.isoformat().replace("+00:00", "Z"),
        "base_url": BASE,
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _clean():
    AccessTokens().clear()
    yield
    AccessTokens().clear()


class TestKeyingOnTheBaseUrl:
    """A token is cached under the canonical base URL intake resolved the
    request to, not under the URL the caller supplied. Lookup is an
    exact-or-path-prefix comparison with the longest match winning."""

    def test_caches_under_the_base_url_intake_returned(self):
        returned = payload("tok-1", base_url="https://example.com/orders")

        with patch(GENERATOR, return_value=returned) as generate:
            assert AccessTokens().token("https://example.com/orders/widgets/42") == "tok-1"
            # A different path under the same registered base URL reuses the entry.
            assert AccessTokens().token("https://example.com/orders/anything") == "tok-1"

        # The caller's URL went out verbatim; the response's base_url became the key.
        assert generate.call_args_list == [call("https://example.com/orders/widgets/42")]

    def test_distinct_base_urls_are_kept_apart(self):
        # The reason the cache is a map at all: a service that calls two targets
        # needs a token for each, and holding one would send the wrong
        # credential to the second.
        issued = {
            "https://a.example.com": payload("tok-a", base_url="https://a.example.com"),
            "https://b.example.com": payload("tok-b", base_url="https://b.example.com"),
        }

        with patch(GENERATOR, side_effect=lambda base_url: issued[base_url]) as generate:
            assert AccessTokens().token("https://a.example.com") == "tok-a"
            assert AccessTokens().token("https://b.example.com") == "tok-b"
            assert AccessTokens().token("https://a.example.com") == "tok-a"

        assert generate.call_count == 2

    def test_longest_matching_prefix_wins(self):
        # Seeded narrow-first: once the broad entry exists nothing under it can
        # miss, so this is the only order in which both entries can be created.
        responses = [
            payload("narrow", base_url="https://example.com/orders"),
            payload("broad", base_url="https://example.com"),
        ]

        with patch(GENERATOR, side_effect=responses) as generate:
            AccessTokens().token("https://example.com/orders/42")
            AccessTokens().token("https://example.com/other")

            assert AccessTokens().token("https://example.com/orders/42") == "narrow"
            assert AccessTokens().token("https://example.com/other") == "broad"
            assert generate.call_count == 2

    def test_prefix_match_respects_segment_boundaries(self):
        # "/ordersXX" must NOT match "/orders" -- a prefix that stops mid-segment
        # is a different resource, and reusing the token would present it to a
        # base URL it was never issued for.
        responses = [
            payload("tok-orders", base_url="https://example.com/orders"),
            payload("tok-other", base_url="https://example.com/ordersXX"),
        ]

        with patch(GENERATOR, side_effect=responses) as generate:
            AccessTokens().token("https://example.com/orders")

            assert AccessTokens().token("https://example.com/ordersXX") == "tok-other"

        assert generate.call_args_list[1] == call("https://example.com/ordersXX")

    def test_a_non_canonical_url_misses_rather_than_guessing(self):
        # The SDK does not normalize -- intake owns that rule. A trailing slash
        # the canonical form does not have costs one extra request, which is
        # cheaper than being wrong.
        responses = [
            payload("tok-1", base_url="https://example.com/orders"),
            payload("tok-2", base_url="https://example.com/orders"),
        ]

        with patch(GENERATOR, side_effect=responses) as generate:
            AccessTokens().token("https://example.com/orders")

            assert AccessTokens().token("https://example.com/Orders") == "tok-2"
            assert generate.call_count == 2

    def test_falls_back_to_the_requested_url_when_the_response_omits_base_url(self):
        # An intake that does not send base_url must not poison every lookup
        # with a None key.
        with patch(GENERATOR, return_value={"token": "tok-1"}) as generate:
            assert AccessTokens().token("https://example.com/orders") == "tok-1"
            assert AccessTokens().token("https://example.com/orders/42") == "tok-1"

        assert generate.call_count == 1


class TestFetchingAToken:
    def test_returns_the_generated_token(self):
        with patch(GENERATOR, return_value=payload("tok-abc")):
            assert AccessTokens().token(BASE) == "tok-abc"

    def test_serves_a_later_call_from_the_cache(self):
        with patch(GENERATOR, return_value=payload("tok-abc")) as generate:
            AccessTokens().token(BASE)
            AccessTokens().token(BASE)

        assert generate.call_count == 1

    def test_keeps_a_token_with_more_than_the_refresh_buffer_left(self):
        with patch(GENERATOR, return_value=payload(expires_in_seconds=600)) as generate:
            AccessTokens().token(BASE)
            AccessTokens().token(BASE)

        assert generate.call_count == 1

    def test_regenerates_a_token_that_is_inside_the_refresh_buffer(self):
        # Refreshing two minutes early is what stops a token expiring mid-flight
        # on a slow request.
        responses = [payload("old", expires_in_seconds=60), payload("new")]

        with patch(GENERATOR, side_effect=responses) as generate:
            assert AccessTokens().token(BASE) == "old"
            assert AccessTokens().token(BASE) == "new"

        assert generate.call_count == 2

    def test_does_not_retain_a_token_that_arrives_already_expired(self):
        with patch(GENERATOR, return_value=payload(expires_in_seconds=-1)):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is False


class TestWhenGenerationFails:
    def test_returns_none_when_the_generator_returns_nothing(self):
        with patch(GENERATOR, return_value=None):
            assert AccessTokens().token(BASE) is None

    def test_returns_none_when_the_payload_carries_an_error_instead_of_a_token(self):
        with patch(GENERATOR, return_value={"error": "invalid client"}):
            assert AccessTokens().token(BASE) is None

    def test_returns_none_when_the_token_field_is_blank(self):
        with patch(GENERATOR, return_value={"token": ""}):
            assert AccessTokens().token(BASE) is None

    def test_discards_the_stale_token_when_a_refresh_fails(self):
        # A failed refresh must not leave the expiring token behind claiming to
        # be usable -- callers would keep presenting it right up to the 401.
        with patch(GENERATOR, return_value=payload(expires_in_seconds=60)):
            AccessTokens().token(BASE)

        with patch(GENERATOR, return_value=None):
            assert AccessTokens().token(BASE) is None

        assert AccessTokens().exists(BASE) is False

    def test_a_failure_leaves_other_base_urls_untouched(self):
        # Only the entry covering the failed URL is dropped. Intake refusing one
        # target must not cost the tokens held for every other target.
        other = "https://other.example.com"

        with patch(GENERATOR, return_value=payload(expires_in_seconds=60)):
            AccessTokens().token(BASE)
        with patch(GENERATOR, return_value=payload("tok-other", base_url=other)):
            AccessTokens().token(other)

        with patch(GENERATOR, return_value=None):
            assert AccessTokens().token(BASE) is None

        assert AccessTokens().exists(BASE) is False
        assert AccessTokens().exists(other) is True

    def test_leaves_a_live_token_alone_because_it_never_asks(self):
        # A live token covering the URL is served without a generation call at
        # all, so intake's opinion of a deeper path cannot disturb it.
        with patch(GENERATOR, return_value=payload("tok-1")) as generate:
            AccessTokens().token(BASE)

            assert AccessTokens().token(BASE + "/42") == "tok-1"
            assert generate.call_count == 1

        assert AccessTokens().exists(BASE) is True

    def test_does_not_cache_the_failure(self):
        with patch(GENERATOR, return_value=None):
            AccessTokens().token(BASE)

        with patch(GENERATOR, return_value=payload("tok-recovered")) as generate:
            assert AccessTokens().token(BASE) == "tok-recovered"
            assert generate.call_count == 1


class TestExists:
    def test_is_false_before_any_token_is_issued(self):
        assert AccessTokens().exists(BASE) is False

    def test_is_true_for_a_freshly_generated_token(self):
        with patch(GENERATOR, return_value=payload()):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True

    def test_is_false_for_a_base_url_no_held_token_covers(self):
        with patch(GENERATOR, return_value=payload()):
            AccessTokens().token(BASE)

        assert AccessTokens().exists("https://elsewhere.example.com") is False

    def test_is_false_when_less_than_thirty_seconds_remain(self):
        # ``Authorization.header`` treats "exists" as "safe to present", so a
        # token about to expire has to read as absent.
        with patch(GENERATOR, return_value=payload(expires_in_seconds=10)):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is False


class TestInvalidate:
    def test_drops_the_current_token(self):
        with patch(GENERATOR, return_value=payload()):
            current = AccessTokens().token(BASE)

        AccessTokens().invalidate(current)

        assert AccessTokens().exists(BASE) is False

    def test_finds_the_entry_by_token_value_and_drops_only_that_one(self):
        # A rejected caller holds a token, not a URL, so the lookup cannot be by
        # base URL -- and the tokens held for other targets are still good.
        a, b = "https://a.example.com", "https://b.example.com"
        issued = {
            a: payload("tok-a", base_url=a),
            b: payload("tok-b", base_url=b),
        }

        with patch(GENERATOR, side_effect=lambda base_url: issued[base_url]):
            AccessTokens().token(a)
            AccessTokens().token(b)

        AccessTokens().invalidate("tok-a")

        assert AccessTokens().exists(a) is False
        assert AccessTokens().exists(b) is True

    def test_ignores_a_token_that_has_already_been_replaced(self):
        # What stops a 401 from stampeding. Every request in flight when a token
        # is rejected reports the same stale value; only the first should cause
        # an exchange, because the rest are holding a token that has already
        # been replaced and clearing for them would discard a good one.
        with patch(GENERATOR, side_effect=[payload("tok-1"), payload("tok-2")]) as generate:
            stale = AccessTokens().token(BASE)
            AccessTokens().invalidate(stale)
            AccessTokens().token(BASE)

            AccessTokens().invalidate(stale)

            assert AccessTokens().token(BASE) == "tok-2"
            assert generate.call_count == 2

    def test_ignores_none(self):
        with patch(GENERATOR, return_value=payload("tok-1")) as generate:
            AccessTokens().token(BASE)

            AccessTokens().invalidate(None)

            assert AccessTokens().token(BASE) == "tok-1"
            assert generate.call_count == 1


class TestClear:
    def test_drops_every_cached_token(self):
        other = "https://other.example.com"

        with patch(GENERATOR, return_value=payload("a")):
            AccessTokens().token(BASE)
        with patch(GENERATOR, return_value=payload("b", base_url=other)):
            AccessTokens().token(other)

        AccessTokens().clear()

        assert AccessTokens().exists(BASE) is False
        assert AccessTokens().exists(other) is False


class TestConcurrency:
    def test_callers_racing_for_a_token_share_one_generation(self):
        # The cached token is read without the lock; the lock is only taken to
        # exchange. A caller that waited for it has to re-read rather than
        # exchange again, or a burst at startup fans out into one call each.
        callers = 8
        start = threading.Barrier(callers)
        results = []
        results_lock = threading.Lock()

        def call_token():
            start.wait()
            value = AccessTokens().token(BASE)
            with results_lock:
                results.append(value)

        with patch(GENERATOR, return_value=payload("tok-1")) as generate:
            threads = [threading.Thread(target=call_token) for _ in range(callers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            assert generate.call_count == 1

        assert results == ["tok-1"] * callers

    def test_reading_one_entry_while_another_is_minted_is_safe(self):
        # The fast path reads the map without the lock, so a write has to
        # replace it rather than mutate it. Mutating in place raises
        # "dictionary changed size during iteration" the moment a second target
        # is minted while another thread is doing a lookup -- which is exactly
        # what a service that calls two targets does all day.
        # A short switch interval forces the interpreter to preempt a reader
        # mid-lookup, which is what makes the collision reproducible rather than
        # a once-a-week production mystery.
        rounds = 300
        errors = []
        errors_lock = threading.Lock()

        def record(fn):
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 - the point is to see any
                with errors_lock:
                    errors.append(exc)

        def mint_new_targets():
            for i in range(rounds):
                AccessTokens().token(f"https://t{i}.example.com")

        def read_the_seeded_target():
            for _ in range(rounds):
                assert AccessTokens().token(BASE) == "seed"

        def generate(base_url):
            return payload("seed" if base_url == BASE else "other", base_url=base_url)

        previous_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            with patch(GENERATOR, side_effect=generate):
                AccessTokens().token(BASE)

                threads = [threading.Thread(target=record, args=(mint_new_targets,))]
                threads += [
                    threading.Thread(target=record, args=(read_the_seeded_target,))
                    for _ in range(4)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
        finally:
            sys.setswitchinterval(previous_interval)

        assert errors == []


class TestTheExpiryTimestamp:
    def test_accepts_the_z_suffixed_form_intake_sends(self):
        with patch(GENERATOR, return_value=payload("t", expired_at="2099-01-01T00:00:00Z")):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True

    def test_accepts_an_explicit_utc_offset(self):
        with patch(GENERATOR, return_value=payload("t", expired_at="2099-01-01T00:00:00+00:00")):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True

    @pytest.mark.parametrize(
        "expired_at", ["not-a-date", "", None, 1893456000], ids=["garbage", "blank", "null", "epoch-int"]
    )
    def test_falls_back_to_an_hour_when_the_expiry_is_unusable(self, expired_at):
        # Falling back to a short life keeps the token usable rather than
        # failing the request outright, and the next hour re-syncs with intake.
        with patch(GENERATOR, return_value=payload("t", expired_at=expired_at)):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True

    def test_falls_back_to_an_hour_when_the_expiry_is_absent(self):
        with patch(GENERATOR, return_value={"token": "t", "base_url": BASE}):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True


class TestTheSingleton:
    def test_every_construction_shares_one_cache(self):
        # Callers construct ``AccessTokens()`` fresh at each use site; if that
        # produced a new cache the token would be regenerated on every request.
        with patch(GENERATOR, return_value=payload()) as generate:
            AccessTokens().token(BASE)
            AccessTokens().token(BASE)

        assert generate.call_count == 1
        assert AccessTokens() is AccessTokens()
