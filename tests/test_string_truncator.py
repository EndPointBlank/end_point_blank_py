"""
``StringTruncator`` bounds what the writers put on the wire. The byte budget is
the point — intake rejects oversized payloads — and the UTF-8 handling is what
stops a truncated body arriving as mojibake.
"""

from end_point_blank.string_truncator import StringTruncator


class TestWhenNoTruncationIsNeeded:
    def test_returns_none_as_an_empty_string(self):
        # The writers pass optional bodies straight through; a None leaking into
        # a JSON payload where a string is expected is an intake-side error.
        assert StringTruncator.truncate(None) == ""

    def test_leaves_a_short_string_alone(self):
        assert StringTruncator.truncate("hello") == "hello"

    def test_leaves_a_string_exactly_at_the_limit_alone(self):
        assert StringTruncator.truncate("abcde", limit=5) == "abcde"

    def test_returns_an_empty_string_unchanged(self):
        assert StringTruncator.truncate("") == ""


class TestWhenTruncationIsNeeded:
    def test_marks_the_result_as_truncated(self):
        result = StringTruncator.truncate("a" * 100, limit=20)

        assert result.endswith("<truncated>")

    def test_stays_within_the_byte_budget(self):
        result = StringTruncator.truncate("a" * 100, limit=20)

        assert len(result.encode("utf-8")) <= 20

    def test_keeps_the_leading_content(self):
        result = StringTruncator.truncate("abcdefghij" * 10, limit=20)

        assert result.startswith("abcdefghi")

    def test_accepts_a_custom_suffix(self):
        result = StringTruncator.truncate("a" * 100, limit=20, suffix="...")

        assert result.endswith("...")
        assert len(result.encode("utf-8")) <= 20


class TestMultiByteCharacters:
    def test_never_splits_a_character_in_half(self):
        # Cutting mid-sequence would put a lone continuation byte on the wire and
        # the whole payload fails to decode downstream, not just the cut string.
        result = StringTruncator.truncate("é" * 50, limit=25)

        assert "�" not in result
        result.encode("utf-8").decode("utf-8")

    def test_measures_the_limit_in_bytes_not_characters(self):
        # 30 two-byte characters are 60 bytes, so a 40-byte budget must truncate
        # even though the string is only 30 characters long.
        result = StringTruncator.truncate("é" * 30, limit=40)

        assert result.endswith("<truncated>")
        assert len(result.encode("utf-8")) <= 40

    def test_handles_characters_wider_than_two_bytes(self):
        result = StringTruncator.truncate("😀" * 20, limit=30)

        assert "�" not in result
        assert len(result.encode("utf-8")) <= 30
