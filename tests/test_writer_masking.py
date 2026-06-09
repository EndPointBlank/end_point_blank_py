import json
from end_point_blank.masking import apply

def test_request_payload_masked_keeps_wire_keys():
    payload = {"headers": {"Authorization": "secret"}, "request": json.dumps({"ssn": "x"}), "path": "/p"}
    rules = [{"target": "request_headers", "path": "$.Authorization", "replacement_value": "***"}]
    out = apply(payload, "request", rules, None)
    assert set(["headers", "request", "path"]).issubset(out.keys())  # no renaming
    assert out["headers"]["Authorization"] == "***"

def test_error_payload_stamps_then_masks_message():
    payload = {"message": "ssn 123-45-6789", "stamped_path": "/u", "stamped_http_method": "GET",
               "stacktrace": ["line1", "line2"]}
    rules = [{"target": "error_message", "regex": r"\d{3}-\d{2}-\d{4}", "replacement_value": "XXX"}]
    out = apply(payload, "error", rules, None)
    assert out["message"] == "ssn XXX"
    assert out["stacktrace"] == ["line1", "line2"]  # never masked
    assert out["stamped_path"] == "/u" and out["stamped_http_method"] == "GET"
