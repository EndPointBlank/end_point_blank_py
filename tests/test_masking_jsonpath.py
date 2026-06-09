from end_point_blank.masking import parse_path, transform

def repl(_old):
    return "X"

def test_root():
    assert transform({"a": 1}, parse_path("$"), repl) == "X"

def test_dot_child():
    assert transform({"user": {"ssn": "abc"}}, parse_path("$.user.ssn"), repl) == {"user": {"ssn": "X"}}

def test_bracket_child():
    assert transform({"a": {"b": 1}}, parse_path("$['a']['b']"), repl) == {"a": {"b": "X"}}

def test_index():
    assert transform({"items": [10, 20]}, parse_path("$.items[1]"), repl) == {"items": [10, "X"]}

def test_wildcard_object():
    assert transform({"a": 1, "b": 2}, parse_path("$.*"), repl) == {"a": "X", "b": "X"}

def test_wildcard_array():
    assert transform({"l": [1, 2]}, parse_path("$.l[*]"), repl) == {"l": ["X", "X"]}

def test_recursive_descent():
    out = transform({"a": {"password": 1}, "b": {"password": 2}}, parse_path("$..password"), repl)
    assert out == {"a": {"password": "X"}, "b": {"password": "X"}}

def test_missing_child_is_noop():
    assert transform({"a": 1}, parse_path("$.zzz"), repl) == {"a": 1}

def test_out_of_range_index_is_noop():
    assert transform({"l": [1]}, parse_path("$.l[5]"), repl) == {"l": [1]}

def test_unsupported_path_returns_none():
    assert parse_path("$.a[?(@.x)]") is None
    assert parse_path("$.a[1:2]") is None
    assert parse_path("") is None
