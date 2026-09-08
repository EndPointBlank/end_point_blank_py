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
from end_point_blank.tokens.token_result import TokenOutcome, TokenResult

GENERATOR = "end_point_blank.commands.generate_access_token.GenerateAccessToken.token_result"

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


def minted_body(body):
    """A 2xx that minted something usable, carrying *body* verbatim."""
    return TokenResult(TokenOutcome.SUCCESS, 201, body)


def broken_2xx(body=None, status=201):
    """intake claiming success and delivering nothing usable -- an unreadable
    body, no token, or a token with no ``base_url`` to key it under.
    ``GenerateAccessToken`` classifies all three as a server error carrying the
    real 2xx status."""
    return TokenResult(TokenOutcome.SERVER_ERROR, status, body)


def minted(token="tok-1", expires_in_seconds=3600, **overrides):
    """The successful mint the cache is built around."""
    return minted_body(payload(token, expires_in_seconds, **overrides))


def rejected(body=None):
    """intake refusing the credential: permanent until it is re-issued."""
    return TokenResult(TokenOutcome.CREDENTIAL_REJECTED, 401, body)


def request_rejected(status=422, body=None):
    """Permanent, but the credential is fine -- intake's 400 and 422 branches."""
    return TokenResult(TokenOutcome.REQUEST_REJECTED, status, body)


def server_error(status=500, body=None):
    return TokenResult(TokenOutcome.SERVER_ERROR, status, body)


# ``post`` has already exhausted its retries by the time this is the answer.
TRANSPORT_FAILURE = TokenResult(TokenOutcome.TRANSPORT_ERROR)


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
        returned = minted("tok-1", base_url="https://example.com/orders")

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
            "https://a.example.com": minted("tok-a", base_url="https://a.example.com"),
            "https://b.example.com": minted("tok-b", base_url="https://b.example.com"),
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
            minted("narrow", base_url="https://example.com/orders"),
            minted("broad", base_url="https://example.com"),
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
            minted("tok-orders", base_url="https://example.com/orders"),
            minted("tok-other", base_url="https://example.com/ordersXX"),
        ]

        with patch(GENERATOR, side_effect=responses) as generate:
            AccessTokens().token("https://example.com/orders")

            assert AccessTokens().token("https://example.com/ordersXX") == "tok-other"

        assert generate.call_args_list[1] == call("https://example.com/ordersXX")

    @pytest.mark.parametrize(
        "requested",
        ["https://example.com/Orders", "https://example.com/orders?page=2"],
        ids=["different-case", "query-string"],
    )
    def test_a_non_canonical_url_misses_rather_than_guessing(self, requested):
        # The SDK does not normalize -- intake owns that rule. A URL that does
        # not match character-for-character costs one extra request, which is
        # cheaper than presenting a token issued for somewhere else. (A query
        # string should have been stripped before it got here; missing is the
        # right answer when it was not.)
        responses = [
            minted("tok-1", base_url="https://example.com/orders"),
            minted("tok-2", base_url="https://example.com/orders"),
        ]

        with patch(GENERATOR, side_effect=responses) as generate:
            AccessTokens().token("https://example.com/orders")

            assert AccessTokens().token(requested) == "tok-2"
            assert generate.call_count == 2

    def test_a_trailing_slash_still_matches(self):
        # Falls out of the "key + /" rule rather than from any normalization:
        # ".../orders/" starts with ".../orders/". Worth pinning, because it is
        # the one non-identical form that does NOT cost an extra mint.
        with patch(GENERATOR, return_value=minted("tok-1", base_url="https://example.com/orders")) as generate:
            AccessTokens().token("https://example.com/orders")

            assert AccessTokens().token("https://example.com/orders/") == "tok-1"
            assert generate.call_count == 1


class TestFetchingAToken:
    def test_returns_the_generated_token(self):
        with patch(GENERATOR, return_value=minted("tok-abc")):
            assert AccessTokens().token(BASE) == "tok-abc"

    def test_serves_a_later_call_from_the_cache(self):
        with patch(GENERATOR, return_value=minted("tok-abc")) as generate:
            AccessTokens().token(BASE)
            AccessTokens().token(BASE)

        assert generate.call_count == 1

    def test_keeps_a_token_with_more_than_the_refresh_buffer_left(self):
        with patch(GENERATOR, return_value=minted(expires_in_seconds=600)) as generate:
            AccessTokens().token(BASE)
            AccessTokens().token(BASE)

        assert generate.call_count == 1

    def test_regenerates_a_token_that_is_inside_the_refresh_buffer(self):
        # Refreshing two minutes early is what stops a token expiring mid-flight
        # on a slow request.
        responses = [minted("old", expires_in_seconds=60), minted("new")]

        with patch(GENERATOR, side_effect=responses) as generate:
            assert AccessTokens().token(BASE) == "old"
            assert AccessTokens().token(BASE) == "new"

        assert generate.call_count == 2

    def test_does_not_retain_a_token_that_arrives_already_expired(self):
        with patch(GENERATOR, return_value=minted(expires_in_seconds=-1)):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is False


class TestWhenGenerationFails:
    def test_returns_none_when_intake_cannot_be_reached(self):
        with patch(GENERATOR, return_value=TRANSPORT_FAILURE):
            assert AccessTokens().token(BASE) is None

    def test_returns_none_when_intake_rejects_the_credential(self):
        with patch(GENERATOR, return_value=rejected({"error": "invalid client"})):
            assert AccessTokens().token(BASE) is None

    def test_returns_none_when_the_token_field_is_blank(self):
        with patch(GENERATOR, return_value=broken_2xx({"token": ""})):
            assert AccessTokens().token(BASE) is None

    def test_a_response_without_a_base_url_is_a_failed_mint(self, caplog):
        # Without a base URL there is no application environment to cache the
        # token under, so no token is handed back either. Keying on the caller's
        # URL instead would store an entry per resource URL, and nothing here
        # evicts -- a bounded extra request traded for an unbounded leak.
        with patch(GENERATOR, return_value=broken_2xx({"token": "tok-1"})) as generate:
            assert AccessTokens().token("https://example.com/orders/1") is None
            assert AccessTokens().token("https://example.com/orders/2") is None

        assert AccessTokens().exists("https://example.com/orders/1") is False
        # Nothing was cached, so the second call had to ask again.
        assert generate.call_count == 2
        # Says what actually happened: a broken server, not a bad request.
        assert "carried a token but no base_url" in caplog.text

    def test_discards_the_stale_token_when_a_refresh_fails(self):
        # A failed refresh must not leave the expiring token behind claiming to
        # be usable -- callers would keep presenting it right up to the 401.
        with patch(GENERATOR, return_value=minted(expires_in_seconds=60)):
            AccessTokens().token(BASE)

        with patch(GENERATOR, return_value=TRANSPORT_FAILURE):
            assert AccessTokens().token(BASE) is None

        assert AccessTokens().exists(BASE) is False

    def test_a_failure_leaves_other_base_urls_untouched(self):
        # Only the entry covering the failed URL is dropped. Intake refusing one
        # target must not cost the tokens held for every other target.
        other = "https://other.example.com"

        with patch(GENERATOR, return_value=minted(expires_in_seconds=60)):
            AccessTokens().token(BASE)
        with patch(GENERATOR, return_value=minted("tok-other", base_url=other)):
            AccessTokens().token(other)

        with patch(GENERATOR, return_value=TRANSPORT_FAILURE):
            assert AccessTokens().token(BASE) is None

        assert AccessTokens().exists(BASE) is False
        assert AccessTokens().exists(other) is True

    def test_leaves_a_live_token_alone_because_it_never_asks(self):
        # A live token covering the URL is served without a generation call at
        # all, so intake's opinion of a deeper path cannot disturb it.
        with patch(GENERATOR, return_value=minted("tok-1")) as generate:
            AccessTokens().token(BASE)

            assert AccessTokens().token(BASE + "/42") == "tok-1"
            assert generate.call_count == 1

        assert AccessTokens().exists(BASE) is True

    def test_does_not_cache_the_failure(self):
        with patch(GENERATOR, return_value=TRANSPORT_FAILURE):
            AccessTokens().token(BASE)

        with patch(GENERATOR, return_value=minted("tok-recovered")) as generate:
            assert AccessTokens().token(BASE) == "tok-recovered"
            assert generate.call_count == 1


class TestExists:
    def test_is_false_before_any_token_is_issued(self):
        assert AccessTokens().exists(BASE) is False

    def test_is_true_for_a_freshly_generated_token(self):
        with patch(GENERATOR, return_value=minted()):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True

    def test_is_true_for_a_sub_path_of_a_cached_base_url(self):
        # ``exists`` goes through the same prefix matcher ``token`` does, so a
        # deeper path under a cached base URL reads as covered. Answering False
        # here would report a token as absent that the very next ``token`` call
        # serves from cache.
        with patch(GENERATOR, return_value=minted()):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE + "/widgets/42") is True

    def test_is_false_for_a_base_url_no_held_token_covers(self):
        with patch(GENERATOR, return_value=minted()):
            AccessTokens().token(BASE)

        assert AccessTokens().exists("https://elsewhere.example.com") is False

    def test_is_false_when_less_than_thirty_seconds_remain(self):
        # ``exists`` answers "is a usable token already held", and a token with
        # seconds left will have expired by the time a request carrying it
        # lands -- so the floor is 30 seconds rather than zero. (It is not the
        # same floor ``token`` refreshes at: that one is two minutes, because
        # refreshing early is cheap and being caught short is not.)
        with patch(GENERATOR, return_value=minted(expires_in_seconds=10)):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is False


class TestInvalidate:
    def test_drops_the_current_token(self):
        with patch(GENERATOR, return_value=minted()):
            current = AccessTokens().token(BASE)

        AccessTokens().invalidate(current)

        assert AccessTokens().exists(BASE) is False

    def test_finds_the_entry_by_token_value_and_drops_only_that_one(self):
        # A rejected caller holds a token, not a URL, so the lookup cannot be by
        # base URL -- and the tokens held for other targets are still good.
        a, b = "https://a.example.com", "https://b.example.com"
        issued = {
            a: minted("tok-a", base_url=a),
            b: minted("tok-b", base_url=b),
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
        with patch(GENERATOR, side_effect=[minted("tok-1"), minted("tok-2")]) as generate:
            stale = AccessTokens().token(BASE)
            AccessTokens().invalidate(stale)
            AccessTokens().token(BASE)

            AccessTokens().invalidate(stale)

            assert AccessTokens().token(BASE) == "tok-2"
            assert generate.call_count == 2

    def test_ignores_none(self):
        with patch(GENERATOR, return_value=minted("tok-1")) as generate:
            AccessTokens().token(BASE)

            AccessTokens().invalidate(None)

            assert AccessTokens().token(BASE) == "tok-1"
            assert generate.call_count == 1


class TestClear:
    def test_drops_every_cached_token(self):
        other = "https://other.example.com"

        with patch(GENERATOR, return_value=minted("a")):
            AccessTokens().token(BASE)
        with patch(GENERATOR, return_value=minted("b", base_url=other)):
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

        with patch(GENERATOR, return_value=minted("tok-1")) as generate:
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
            return minted("seed" if base_url == BASE else "other", base_url=base_url)

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
        with patch(GENERATOR, return_value=minted("t", expired_at="2099-01-01T00:00:00Z")):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True

    def test_accepts_an_explicit_utc_offset(self):
        with patch(GENERATOR, return_value=minted("t", expired_at="2099-01-01T00:00:00+00:00")):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True

    @pytest.mark.parametrize(
        "expired_at", ["not-a-date", "", None, 1893456000], ids=["garbage", "blank", "null", "epoch-int"]
    )
    def test_falls_back_to_an_hour_when_the_expiry_is_unusable(self, expired_at):
        # Falling back to a short life keeps the token usable rather than
        # failing the request outright, and the next hour re-syncs with intake.
        with patch(GENERATOR, return_value=minted("t", expired_at=expired_at)):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True

    def test_falls_back_to_an_hour_when_the_expiry_is_absent(self):
        with patch(GENERATOR, return_value=minted_body({"token": "t", "base_url": BASE})):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True


class TestANilBaseUrl:
    """``token(None)`` / ``exists(None)`` used to behave differently depending
    on cache state, because the match loop only touches its argument once
    there is something to iterate over:

    - cold cache: the loop body never runs, nothing raises, and the call
      proceeds to mint with a null base URL.
    - warm cache: the loop body runs and ``None.startswith(...)`` raises
      ``AttributeError``.

    Same call, two outcomes, decided entirely by unrelated earlier traffic.
    Both must now take the same "no match" miss path -- the warm-cache case
    is the one that matters, because a cold cache never raised in the first
    place and would pass without the fix."""

    def test_a_cold_cache_does_not_raise_and_mints_with_the_nil_url(self):
        with patch(GENERATOR, return_value=TRANSPORT_FAILURE) as generate:
            assert AccessTokens().token(None) is None

        assert generate.call_args == call(None)

    def test_a_warm_cache_reaches_the_same_outcome_as_a_cold_one(self):
        # Seed an entry first so the match loop has something to iterate --
        # this is the case that used to raise AttributeError instead of
        # reaching the generator at all.
        with patch(GENERATOR, return_value=minted("tok-1")):
            AccessTokens().token(BASE)

        with patch(GENERATOR, return_value=TRANSPORT_FAILURE) as generate:
            assert AccessTokens().token(None) is None

        assert generate.call_args == call(None)
        # The unrelated warm entry must survive untouched.
        assert AccessTokens().exists(BASE) is True

    def test_exists_is_false_for_none_with_a_warm_cache(self):
        with patch(GENERATOR, return_value=minted("tok-1")):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(None) is False


class TestARefreshThatResolvesToADifferentCanonicalBaseUrl:
    """The success path used to add the new key without removing the stale
    entry it had just matched -- only the failure path deleted. So when an
    entry stored under a longer key is refreshed and intake answers with a
    shorter, different canonical base URL, the old key survives and, being
    longer, keeps winning the match forever: a permanent mint-on-every-call
    storm that only a process restart clears."""

    OLD = "https://x.example.com/orders"
    NEW = "https://x.example.com"

    def _seed_and_refresh_to_a_shorter_url(self):
        # Seed under the longer key with a short TTL so the very next lookup
        # finds it inside the refresh buffer and triggers a mint.
        with patch(GENERATOR, return_value=minted("old", base_url=self.OLD, expires_in_seconds=60)):
            AccessTokens().token(self.OLD)

        # Refresh resolves to a shorter, different canonical base URL.
        with patch(GENERATOR, return_value=minted("new", base_url=self.NEW, expires_in_seconds=3600)):
            assert AccessTokens().token(self.OLD) == "new"

    def test_the_matched_key_is_removed_once_the_canonical_url_changes(self):
        self._seed_and_refresh_to_a_shorter_url()

        assert self.OLD not in AccessTokens()._entries

    def test_a_follow_up_call_is_served_from_cache_rather_than_minting_again(self):
        # The actual harm: if the stale, longer key survives, it shadows the
        # good, shorter one -- longest match wins -- and every subsequent
        # call re-mints because the shadowing entry is still stale.
        self._seed_and_refresh_to_a_shorter_url()

        with patch(GENERATOR, return_value=minted("should-not-be-minted")) as generate:
            assert AccessTokens().token(self.OLD) == "new"

        assert generate.call_count == 0


class TestTheSingleton:
    def test_every_construction_shares_one_cache(self):
        # Callers construct ``AccessTokens()`` fresh at each use site; if that
        # produced a new cache the token would be regenerated on every request.
        with patch(GENERATOR, return_value=minted()) as generate:
            AccessTokens().token(BASE)
            AccessTokens().token(BASE)

        assert generate.call_count == 1
        assert AccessTokens() is AccessTokens()


class TestBranchingOnWhyAMintFailed:
    """A caller that cannot tell a rejected credential from a broken intake
    retries forever against a credential that will never work again, and gives
    up on an outage that would have cleared. ``token`` still answers None for
    all of them -- the reason is recorded instead, and queried separately."""

    def test_a_rejected_credential_is_recorded_as_such(self):
        with patch(GENERATOR, return_value=rejected({"error": "invalid client"})):
            assert AccessTokens().token(BASE) is None

        failure = AccessTokens().last_failure(BASE)
        assert failure.outcome is TokenOutcome.CREDENTIAL_REJECTED
        assert failure.status == 401

    def test_a_rejected_credential_is_logged_loudly_and_distinctly(self, caplog):
        # Not the generic "Failed to generate access token" line: this one names
        # the remedy, because no amount of retrying is the remedy.
        with patch(GENERATOR, return_value=rejected({"error": "invalid client"})):
            AccessTokens().token(BASE)

        assert "rejected" in caplog.text
        assert "re-issue" in caplog.text
        assert BASE in caplog.text
        assert "Failed to generate access token" not in caplog.text

    def test_a_rejected_request_is_kept_apart_from_a_server_failure(self, caplog):
        # intake answers 422 when the URL resolves to no registered environment.
        # Permanent like a 401, but the credential is fine -- so it must not be
        # reported as a rejected credential either.
        with patch(GENERATOR, return_value=request_rejected(422, {"error": "Missing target application"})):
            assert AccessTokens().token(BASE) is None

        failure = AccessTokens().last_failure(BASE)
        assert failure.outcome is TokenOutcome.REQUEST_REJECTED
        assert failure.status == 422
        assert "re-issue" not in caplog.text

    def test_a_server_failure_is_recorded_as_retryable(self, caplog):
        with patch(GENERATOR, return_value=server_error(503)):
            assert AccessTokens().token(BASE) is None

        failure = AccessTokens().last_failure(BASE)
        assert failure.outcome is TokenOutcome.SERVER_ERROR
        assert failure.status == 503
        assert "Failed to generate access token" in caplog.text
        assert "re-issue" not in caplog.text

    def test_a_transport_failure_is_recorded_as_retryable(self):
        with patch(GENERATOR, return_value=TRANSPORT_FAILURE):
            assert AccessTokens().token(BASE) is None

        failure = AccessTokens().last_failure(BASE)
        assert failure.outcome is TokenOutcome.TRANSPORT_ERROR
        assert failure.status is None

    def test_the_log_leads_with_the_status_of_a_non_2xx(self, caplog):
        # A caller reading logs, rather than ``last_failure``, still has to be
        # able to tell 503 from 422.
        with patch(GENERATOR, return_value=server_error(503, {"error": "upstream down"})):
            AccessTokens().token(BASE)

        assert "HTTP 503: upstream down" in caplog.text

    def test_a_success_status_with_an_unreadable_body_says_so(self, caplog):
        # It arrives as a SERVER_ERROR carrying a 2xx status. "HTTP 201" alone
        # would read as a contradiction in the log.
        with patch(GENERATOR, return_value=broken_2xx()):
            assert AccessTokens().token(BASE) is None

        assert "HTTP 201 with an unreadable body" in caplog.text
        assert AccessTokens().last_failure(BASE).outcome is TokenOutcome.SERVER_ERROR

    def test_a_2xx_carrying_only_a_message_logs_that_message(self, caplog):
        # No status to lead with -- intake said it worked -- so whatever it did
        # say is the most useful thing in the line.
        with patch(GENERATOR, return_value=broken_2xx({"error": "nothing to mint"})):
            assert AccessTokens().token(BASE) is None

        assert "nothing to mint" in caplog.text

    def test_a_2xx_that_carries_no_usable_token_is_still_a_failure(self):
        # The broken-server case: a success status with nothing to cache under.
        # It has to be recorded too, or a caller polling ``last_failure`` after
        # a None sees nothing and concludes the mint worked.
        with patch(GENERATOR, return_value=broken_2xx({"token": "tok-1"})):
            assert AccessTokens().token(BASE) is None

        failure = AccessTokens().last_failure(BASE)
        assert failure.outcome is TokenOutcome.SERVER_ERROR
        # The real 2xx rides along, so a reader can tell this apart from a 500.
        assert failure.status == 201

    def test_a_non_2xx_body_that_happens_to_carry_a_token_is_not_cached(self):
        # Only a success mints. A 401 body shaped like a token response is not a
        # token, and caching it would present a credential intake just refused.
        body = {"token": "tok-1", "base_url": BASE, "expired_at": "2099-01-01T00:00:00Z"}

        with patch(GENERATOR, return_value=rejected(body)):
            assert AccessTokens().token(BASE) is None

        assert AccessTokens().exists(BASE) is False


class TestLastFailure:
    def test_is_none_before_anything_has_failed(self):
        assert AccessTokens().last_failure(BASE) is None

    def test_is_none_after_a_mint_that_worked(self):
        with patch(GENERATOR, return_value=minted("tok-1")):
            AccessTokens().token(BASE)

        assert AccessTokens().last_failure(BASE) is None

    def test_is_cleared_by_a_later_success(self):
        # Otherwise a caller that consults it on every None keeps acting on a
        # rejection the credential rotation already fixed.
        with patch(GENERATOR, return_value=rejected()):
            AccessTokens().token(BASE)
        with patch(GENERATOR, return_value=minted("tok-1")):
            assert AccessTokens().token(BASE) == "tok-1"

        assert AccessTokens().last_failure(BASE) is None

    def test_answers_for_a_sub_path_of_the_url_that_failed(self):
        # Callers ask with the URL they are about to call, which is rarely the
        # exact string a previous failure was recorded under.
        with patch(GENERATOR, return_value=rejected()):
            AccessTokens().token(BASE)

        assert AccessTokens().last_failure(BASE + "/widgets/42") is not None

    def test_keeps_failures_for_different_targets_apart(self):
        other = "https://other.example.com"

        with patch(GENERATOR, return_value=rejected()):
            AccessTokens().token(BASE)
        with patch(GENERATOR, return_value=server_error(500)):
            AccessTokens().token(other)

        assert AccessTokens().last_failure(BASE).outcome is TokenOutcome.CREDENTIAL_REJECTED
        assert AccessTokens().last_failure(other).outcome is TokenOutcome.SERVER_ERROR

    def test_records_the_most_recent_failure_for_a_target(self):
        with patch(GENERATOR, return_value=server_error(500)):
            AccessTokens().token(BASE)
        with patch(GENERATOR, return_value=rejected()):
            AccessTokens().token(BASE)

        assert AccessTokens().last_failure(BASE).outcome is TokenOutcome.CREDENTIAL_REJECTED

    def test_is_dropped_by_clear(self):
        with patch(GENERATOR, return_value=rejected()):
            AccessTokens().token(BASE)

        AccessTokens().clear()

        assert AccessTokens().last_failure(BASE) is None

    def test_is_none_for_a_nil_url(self):
        with patch(GENERATOR, return_value=rejected()):
            AccessTokens().token(BASE)

        assert AccessTokens().last_failure(None) is None

    def test_does_not_grow_without_bound(self):
        # A caller looping over distinct resource URLs that all fail would
        # otherwise leak one record per URL -- the same unbounded-map trap the
        # token cache is keyed to avoid. Past the cap the map restarts.
        with patch(GENERATOR, return_value=server_error(500)):
            for i in range(AccessTokens._FAILURE_CAP * 3):
                AccessTokens().token(f"https://t{i}.example.com/orders")

        assert len(AccessTokens()._failures) <= AccessTokens._FAILURE_CAP
        # The newest one is always the one kept.
        last = AccessTokens._FAILURE_CAP * 3 - 1
        assert AccessTokens().last_failure(f"https://t{last}.example.com/orders") is not None


class TestTheLegacyContract:
    """``token`` and ``exists`` are published API. The status plumbing is
    additive: their return values are exactly what they were."""

    def test_token_still_answers_a_bare_string(self):
        with patch(GENERATOR, return_value=minted("tok-abc")):
            assert AccessTokens().token(BASE) == "tok-abc"

    def test_token_still_answers_none_on_every_kind_of_failure(self):
        for result in (rejected(), request_rejected(400), server_error(500), broken_2xx(), TRANSPORT_FAILURE):
            AccessTokens().clear()
            with patch(GENERATOR, return_value=result):
                assert AccessTokens().token(BASE) is None

    def test_exists_still_answers_a_bool(self):
        with patch(GENERATOR, return_value=minted("tok-abc")):
            AccessTokens().token(BASE)

        assert AccessTokens().exists(BASE) is True
        assert AccessTokens().exists("https://elsewhere.example.com") is False
