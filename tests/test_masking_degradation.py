"""
The masking contract is that masking NEVER raises: a malformed path, an
uncompilable regex, a missing field or an unexpected type all degrade to a
no-op. That matters more than it sounds — masking runs inside every writer, so
a raise here takes out the request it is observing, and a rule the operator
typed wrong must fail visibly as "not masked" rather than invisibly as
"request dropped".

Well-formed paths are covered in ``tests/test_masking_jsonpath.py``.
"""

import re

import pytest

from end_point_blank.masking import apply, parse_path, regex_replace_leaves, transform


def repl(_old):
    return "X"


class TestPathsThatMatchNothing:
    @pytest.mark.parametrize(
        "path",
        [
            None, 123, [], {},                # not a string at all
            "", "user.ssn", "$$",             # missing or doubled root
            "$.", "$.[", "$..", "$..[",       # a separator with no name after it
            "$['a", '$["a', "$[a]", "$[",     # unclosed or unquoted brackets
            "$user",                          # no separator after the root
        ],
        ids=lambda p: repr(p),
    )
    def test_an_unusable_path_is_parsed_as_nothing(self, path):
        assert parse_path(path) is None

    def test_a_path_that_matches_nothing_leaves_the_value_alone(self):
        value = {"user": {"ssn": "123-45-6789"}}

        assert transform(value, parse_path("$.["), repl) == value

    def test_a_double_quoted_child_is_supported(self):
        # Operators write rules by hand; both quote styles are valid JSONPath and
        # rejecting one would silently disable the rule.
        assert transform({"a": {"b": 1}}, parse_path('$["a"]["b"]'), repl) == {"a": {"b": "X"}}


class TestTransformOnUnexpectedShapes:
    def test_a_wildcard_over_a_scalar_leaves_it_alone(self):
        assert transform(5, parse_path("$.*"), repl) == 5

    def test_a_child_lookup_into_a_scalar_leaves_it_alone(self):
        assert transform("a string", parse_path("$.user"), repl) == "a string"

    def test_an_index_into_an_object_leaves_it_alone(self):
        assert transform({"a": 1}, parse_path("$[0]"), repl) == {"a": 1}

    def test_a_negative_index_is_not_a_supported_selector(self):
        assert parse_path("$.items[-1]") is None

    def test_recursive_descent_reaches_into_lists(self):
        # Collections of records are the normal shape of an API response, so a
        # descent that stopped at the first array would mask almost nothing.
        value = {"users": [{"ssn": "a"}, {"ssn": "b"}]}

        assert transform(value, parse_path("$..ssn"), repl) == {"users": [{"ssn": "X"}, {"ssn": "X"}]}

    def test_recursive_descent_over_a_scalar_leaves_it_alone(self):
        assert transform(5, parse_path("$..ssn"), repl) == 5


class TestRulesThatCannotBeApplied:
    def test_a_rule_that_is_not_a_mapping_is_skipped(self):
        payload = {"message": "ssn 123-45-6789"}

        assert apply(payload, "error", ["not a rule", None, 42], None) == payload

    def test_a_disabled_rule_is_skipped(self):
        payload = {"message": "secret"}
        rule = {"target": "error_message", "regex": "secret", "replacement_value": "X", "enabled": False}

        assert apply(payload, "error", [rule], None) == payload

    def test_a_rule_for_a_field_the_payload_does_not_have_is_skipped(self):
        # Rules are configured once and applied to every record type; a request
        # rule meeting a response payload is normal, not an error.
        payload = {"status": 200}
        rule = {"target": "response_body", "regex": "x", "replacement_value": "X"}

        assert apply(payload, "response", [rule], None) == payload

    def test_a_rule_for_a_null_field_is_skipped(self):
        payload = {"body": None}
        rule = {"target": "response_body", "regex": "x", "replacement_value": "X"}

        assert apply(payload, "response", [rule], None) == payload

    def test_a_rule_for_an_unknown_record_type_is_skipped(self):
        payload = {"message": "secret"}
        rule = {"target": "error_message", "regex": "secret", "replacement_value": "X"}

        assert apply(payload, "not-a-record-type", [rule], None) == payload

    def test_a_rule_with_an_unknown_target_is_skipped(self):
        payload = {"message": "secret"}
        rule = {"target": "no_such_target", "regex": "secret", "replacement_value": "X"}

        assert apply(payload, "error", [rule], None) == payload

    def test_an_uncompilable_regex_leaves_the_value_alone(self):
        payload = {"message": "secret"}
        rule = {"target": "error_message", "regex": "[unclosed", "replacement_value": "X"}

        assert apply(payload, "error", [rule], None) == payload

    def test_a_rule_with_neither_path_nor_regex_leaves_the_value_alone(self):
        payload = {"message": "secret"}

        assert apply(payload, "error", [{"target": "error_message"}], None) == payload

    def test_no_rules_at_all_leaves_the_payload_alone(self):
        payload = {"message": "secret"}

        assert apply(payload, "error", None, None) == payload

    def test_a_field_that_is_neither_text_nor_a_map_is_left_alone(self):
        payload = {"body": 12345}
        rule = {"target": "response_body", "regex": r"\d+", "replacement_value": "X"}

        assert apply(payload, "response", [rule], None) == payload

    def test_a_body_that_is_not_json_still_gets_regex_masking(self):
        # A non-JSON body is the case where masking matters most — nobody wrote a
        # path rule for it, and the regex is the only thing standing between a
        # secret in a form-encoded body and the log store.
        payload = {"body": "token=abc123&user=ada"}
        rule = {"target": "response_body", "regex": "abc123", "replacement_value": "[redacted]"}

        assert apply(payload, "response", [rule], None)["body"] == "token=[redacted]&user=ada"


class TestRegexOverContainers:
    def test_substitutes_in_every_string_in_a_list(self):
        assert regex_replace_leaves(["a1", "b2"], re.compile(r"\d"), "#") == ["a#", "b#"]

    def test_leaves_non_string_leaves_alone(self):
        value = {"n": 42, "b": True, "z": None}

        assert regex_replace_leaves(value, re.compile(r"\d"), "#") == value


class TestTheMaskHook:
    def test_runs_after_the_rules(self):
        payload = {"message": "ssn 123-45-6789"}
        rule = {"target": "error_message", "regex": r"\d{3}-\d{2}-\d{4}", "replacement_value": "[redacted]"}
        seen = []

        apply(payload, "error", [rule], lambda p, record_type: seen.append(p) or p)

        assert seen[0]["message"] == "ssn [redacted]"

    def test_its_return_value_is_what_gets_written(self):
        result = apply({"message": "x"}, "error", [], lambda p, record_type: {"replaced": True})

        assert result == {"replaced": True}

    def test_a_hook_that_is_not_callable_is_ignored(self):
        payload = {"message": "x"}

        assert apply(payload, "error", [], "not callable") == payload
