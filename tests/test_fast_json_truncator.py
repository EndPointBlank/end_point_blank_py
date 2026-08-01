"""
``FastJsonTruncator`` bounds JSON bodies before they are written. The caps exist
so a single pathological request body cannot blow the payload budget for the
whole batch it is sent in.
"""

import json

from end_point_blank.fast_json_truncator import FastJsonTruncator


def truncated(data, **kwargs):
    return json.loads(FastJsonTruncator.truncate(data, **kwargs))


class TestSmallValues:
    def test_round_trips_a_flat_object(self):
        assert truncated({"name": "ada", "age": 36}) == {"name": "ada", "age": 36}

    def test_round_trips_a_list(self):
        assert truncated([1, 2, 3]) == [1, 2, 3]

    def test_preserves_scalars_of_every_json_type(self):
        value = {"i": 1, "f": 1.5, "b": True, "n": None, "s": "x"}

        assert truncated(value) == value

    def test_keeps_non_ascii_readable_rather_than_escaping_it(self):
        # ensure_ascii=False keeps the byte accounting honest: an escaped "é"
        # is six bytes where the character is two, so escaping would make the
        # budget check measure the wrong thing.
        assert "é" in FastJsonTruncator.truncate({"name": "café"})


class TestTheDepthCap:
    def test_keeps_structure_within_the_depth_limit(self):
        value = {"a": {"b": {"c": {"d": "leaf"}}}}

        assert truncated(value) == value

    def test_replaces_anything_nested_too_deeply(self):
        value = {"a": {"b": {"c": {"d": {"e": {"f": "unreachable"}}}}}}

        assert truncated(value)["a"]["b"]["c"]["d"]["e"]["f"] == "[truncated]"

    def test_stops_a_deeply_nested_list_too(self):
        value = [[[[[[["deep"]]]]]]]

        assert truncated(value) == [[[[[["[truncated]"]]]]]]


class TestTheListCap:
    def test_keeps_the_first_twenty_items(self):
        assert truncated(list(range(100))) == list(range(20))

    def test_leaves_a_list_at_the_cap_intact(self):
        assert truncated(list(range(20))) == list(range(20))


class TestTheKeyCap:
    def test_keeps_the_first_twenty_keys(self):
        value = {f"k{i}": i for i in range(100)}

        result = truncated(value)

        assert len(result) == 20
        assert result["k0"] == 0
        assert "k20" not in result


class TestTheStringCap:
    def test_shortens_a_long_string_leaf(self):
        result = truncated({"body": "x" * 1000})["body"]

        assert result.endswith("...")
        assert len(result) < 1000

    def test_leaves_a_short_string_leaf_alone(self):
        assert truncated({"body": "short"})["body"] == "short"

    def test_shortens_strings_inside_lists(self):
        assert truncated(["x" * 1000])[0].endswith("...")

    def test_never_splits_a_multi_byte_character(self):
        # The cap is applied to the encoded bytes, so a character straddling the
        # boundary has to be dropped whole or the JSON will not decode.
        result = truncated({"body": "é" * 500})["body"]

        assert "�" not in result


class TestTheOverallByteBudget:
    def test_leaves_a_payload_inside_the_budget_untouched(self):
        assert FastJsonTruncator.truncate({"a": "b"}, limit=1000) == '{"a": "b"}'

    def test_marks_a_payload_that_overruns_the_budget(self):
        # The cut here is mid-JSON, so the result is deliberately not valid JSON;
        # the marker is what tells intake the body was cut rather than malformed.
        result = FastJsonTruncator.truncate({f"k{i}": "v" * 100 for i in range(10)}, limit=200)

        assert result.endswith('...,"truncated":true}')
