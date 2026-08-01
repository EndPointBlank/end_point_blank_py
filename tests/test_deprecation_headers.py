"""
The vectors below are the shared set from app_portal's
docs/superpowers/specs/2026-08-01-header-vectors.md. The same table is asserted
in every SDK, so a Python date format that differs from Ruby's by a leading zero
is a test failure here rather than a subtly non-compliant header a customer
finds.

Row 1 is RFC 9745's own worked example. It is the reason to trust the rest.
"""

from datetime import datetime, timezone

import pytest

from end_point_blank import deprecation_headers
from end_point_blank.request_store import RequestStore

VECTORS = [
    ("2023-06-30T23:59:59Z", "@1688169599", "Fri, 30 Jun 2023 23:59:59 GMT"),
    ("2026-01-01T00:00:00Z", "@1767225600", "Thu, 01 Jan 2026 00:00:00 GMT"),
    ("2026-03-09T05:00:00Z", "@1773032400", "Mon, 09 Mar 2026 05:00:00 GMT"),
    ("2026-08-01T14:15:16Z", "@1785593716", "Sat, 01 Aug 2026 14:15:16 GMT"),
    ("2026-11-11T11:11:11Z", "@1794395471", "Wed, 11 Nov 2026 11:11:11 GMT"),
]


@pytest.mark.parametrize("iso,deprecation,sunset", VECTORS)
def test_shared_vectors(iso, deprecation, sunset):
    headers = deprecation_headers.build({"deprecated_at": iso, "sunset_at": iso})

    assert headers["Deprecation"] == deprecation
    assert headers["Sunset"] == sunset


class TestRfcConformance:
    def test_zero_pads_the_day_of_month(self):
        headers = deprecation_headers.build({"sunset_at": "2026-01-01T00:00:00Z"})
        assert " 01 Jan " in headers["Sunset"]

    def test_always_says_gmt(self):
        headers = deprecation_headers.build({"sunset_at": "2026-01-01T00:00:00Z"})

        assert headers["Sunset"].endswith(" GMT")
        assert "UTC" not in headers["Sunset"]
        assert "+00" not in headers["Sunset"]

    def test_converts_a_non_utc_input_rather_than_relabelling_it(self):
        # 2026-01-01T00:00:00+02:00 is 2025-12-31T22:00:00Z.
        headers = deprecation_headers.build({"sunset_at": "2026-01-01T00:00:00+02:00"})
        assert headers["Sunset"] == "Wed, 31 Dec 2025 22:00:00 GMT"

    def test_deprecation_is_unquoted_with_no_subsecond_precision(self):
        headers = deprecation_headers.build({"deprecated_at": "2023-06-30T23:59:59.750Z"})
        assert headers["Deprecation"] == "@1688169599"

    def test_accepts_a_datetime_as_well_as_a_string(self):
        headers = deprecation_headers.build(
            {"deprecated_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        )
        assert headers["Deprecation"] == "@1767225600"


class TestPartialAndAbsentInput:
    def test_deprecation_alone_when_there_is_no_sunset_date(self):
        # The normal starting state: going away, no deadline committed yet.
        headers = deprecation_headers.build(
            {"deprecated_at": "2026-01-01T00:00:00Z", "sunset_at": None}
        )

        assert "Deprecation" in headers
        assert "Sunset" not in headers

    @pytest.mark.parametrize("value", [None, {}, "nonsense", [1, 2, 3], 42])
    def test_returns_nothing_for(self, value):
        assert deprecation_headers.build(value) == {}


class TestNeverProducingAPlausibleHeaderFromNonsense:
    def test_ignores_a_numeric_timestamp(self):
        # datetime.fromtimestamp(12345) is a valid date in 1970. Coercing would
        # turn a nonsense value into a header that looks right and is wrong.
        assert deprecation_headers.build({"deprecated_at": 12345}) == {}

    def test_ignores_a_malformed_timestamp(self):
        assert deprecation_headers.build({"deprecated_at": "not a date"}) == {}

    def test_still_emits_the_good_half(self):
        headers = deprecation_headers.build(
            {"deprecated_at": "2026-01-01T00:00:00Z", "sunset_at": "garbage"}
        )

        assert headers["Deprecation"] == "@1767225600"
        assert "Sunset" not in headers


class TestApply:
    def test_appends_to_existing_headers(self):
        result = deprecation_headers.apply(
            [("Content-Type", "application/json")],
            {"deprecated_at": "2026-01-01T00:00:00Z", "sunset_at": "2026-11-11T11:11:11Z"},
        )

        assert ("Content-Type", "application/json") in result
        assert ("Deprecation", "@1767225600") in result
        assert ("Sunset", "Wed, 11 Nov 2026 11:11:11 GMT") in result

    def test_returns_the_headers_unchanged_when_nothing_is_deprecated(self):
        original = [("Content-Type", "application/json")]
        assert deprecation_headers.apply(original, None) == original

    def test_does_not_override_a_header_the_application_already_set(self):
        # An app that sets its own Sunset has said something more specific than
        # we know about that particular response.
        result = deprecation_headers.apply(
            [("Sunset", "Mon, 01 Jan 2035 00:00:00 GMT")],
            {"sunset_at": "2026-11-11T11:11:11Z"},
        )

        assert result == [("Sunset", "Mon, 01 Jan 2035 00:00:00 GMT")]


class TestRequestStoreIsolation:
    """The store keys off the WSGI environ rather than a thread-local, so one
    request cannot read another's data even if ``clear`` never runs."""

    def teardown_method(self):
        RequestStore.clear()

    def test_round_trips_within_a_request(self):
        RequestStore.set({})
        RequestStore.set_deprecation({"deprecated_at": "2026-01-01T00:00:00Z"})

        assert RequestStore.get_deprecation() == {"deprecated_at": "2026-01-01T00:00:00Z"}

    def test_is_not_visible_to_the_next_request_on_the_same_thread(self):
        RequestStore.set({})
        RequestStore.set_deprecation({"deprecated_at": "2026-01-01T00:00:00Z"})
        RequestStore.set_source_application_environment_id("app-env-123")

        # The next request installs its own environ. No clear() in between —
        # that is the point: isolation must not depend on cleanup running.
        RequestStore.set({})

        assert RequestStore.get_deprecation() is None
        assert RequestStore.get_source_application_environment_id() is None

    def test_is_a_no_op_outside_a_request(self):
        RequestStore.clear()

        RequestStore.set_deprecation({"deprecated_at": "2026-01-01T00:00:00Z"})

        assert RequestStore.get_deprecation() is None

    def test_values_live_in_the_environ_itself(self):
        environ = {}
        RequestStore.set(environ)
        RequestStore.set_deprecation({"deprecated_at": "2026-01-01T00:00:00Z"})

        assert any("deprecation" in key for key in environ)
