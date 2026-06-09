import json
from end_point_blank.masking import apply

def rule(**kw):
    return {"target": kw["target"], "path": kw.get("path"), "regex": kw.get("regex"),
            "replacement_value": kw.get("replacement_value", "...")}

def test_path_only_on_body():
    payload = {"request": json.dumps({"user": {"ssn": "abc"}})}
    out = apply(payload, "request", [rule(target="request_body", path="$.user.ssn", replacement_value="***")], None)
    assert json.loads(out["request"]) == {"user": {"ssn": "***"}}

def test_recursive_descent_on_body():
    payload = {"request": json.dumps({"a": {"password": 1}, "b": {"password": 2}})}
    out = apply(payload, "request", [rule(target="request_body", path="$..password", replacement_value="***")], None)
    assert json.loads(out["request"]) == {"a": {"password": "***"}, "b": {"password": "***"}}

def test_path_plus_regex_scoped():
    payload = {"request": json.dumps({"note": "ssn 123-45-6789"})}
    out = apply(payload, "request", [rule(target="request_body", path="$.note", regex=r"\d{3}-\d{2}-\d{4}", replacement_value="XXX")], None)
    assert json.loads(out["request"]) == {"note": "ssn XXX"}

def test_regex_only_leaves():
    payload = {"request": json.dumps({"a": "x 123-45-6789", "b": "y"})}
    out = apply(payload, "request", [rule(target="request_body", regex=r"\d{3}-\d{2}-\d{4}", replacement_value="XXX")], None)
    assert json.loads(out["request"]) == {"a": "x XXX", "b": "y"}

def test_header_target_key():
    payload = {"headers": {"Authorization": "secret", "Accept": "json"}}
    out = apply(payload, "request", [rule(target="request_headers", path="$.Authorization", replacement_value="***")], None)
    assert out["headers"] == {"Authorization": "***", "Accept": "json"}

def test_error_message_regex():
    payload = {"message": "card 4111-1111-1111-1234 declined"}
    out = apply(payload, "error", [rule(target="error_message", regex=r"(\d{4})-\d{4}-\d{4}-(\d{4})", replacement_value="$1-****-****-$2")], None)
    assert out["message"] == "card 4111-****-****-1234 declined"

def test_non_json_body_regex_on_raw():
    payload = {"request": "raw 123-45-6789"}
    out = apply(payload, "request", [rule(target="request_body", regex=r"\d{3}-\d{2}-\d{4}", replacement_value="XXX")], None)
    assert out["request"] == "raw XXX"

def test_path_noop_on_plain_string():
    payload = {"path": "/users/123-45-6789"}
    out = apply(payload, "request", [rule(target="path", path="$.x", replacement_value="_")], None)
    assert out["path"] == "/users/123-45-6789"

def test_bad_regex_is_noop():
    payload = {"request": json.dumps({"a": "x"})}
    out = apply(payload, "request", [rule(target="request_body", regex="(", replacement_value="_")], None)
    assert json.loads(out["request"]) == {"a": "x"}

def test_hook_runs_last():
    payload = {"request": "{}"}
    def hook(p, rt):
        p["hooked"] = rt
        return p
    out = apply(payload, "request", [], hook)
    assert out["hooked"] == "request"

def test_disabled_rule_skipped():
    payload = {"request": json.dumps({"a": "x"})}
    r = rule(target="request_body", path="$.a", replacement_value="_")
    r["enabled"] = False
    out = apply(payload, "request", [r], None)
    assert json.loads(out["request"]) == {"a": "x"}
