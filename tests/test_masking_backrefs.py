import re
from end_point_blank.masking import regex_replace_all

def rr(pattern, s, template):
    return regex_replace_all(re.compile(pattern), s, template)

def test_named_groups():
    assert rr(r"(\d{3})-(\d{2})-(\d{4})", "123-45-6789", "$1-XX-XXXX") == "123-XX-XXXX"

def test_card():
    assert rr(r"(\d{4})-\d{4}-\d{4}-(\d{4})", "4111-1111-1111-1234", "$1-****-****-$2") == "4111-****-****-1234"

def test_global_multi_match():
    assert rr(r"(\d)", "ab1c2", "[$1]") == "ab[1]c[2]"

def test_swap():
    assert rr(r"(\d+)-(\d+)", "12-34", "$2/$1") == "34/12"

def test_missing_group_is_empty():
    assert rr(r"(\d+)", "42", "$3") == ""

def test_dollar_literal():
    assert rr(r"\d", "5", "$$") == "$"

def test_lone_dollar_literal():
    assert rr(r"\d", "5", "a$b") == "a$b"

def test_multi_digit_group():
    # group 12 doesn't exist here -> empty
    assert rr(r"(\d)", "7", "$12") == ""
