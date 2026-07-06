"""
Contract: stacktrace must be sent as a list of frame strings, never a single
"\n"-joined string. Intake stores + hashes the stacktrace expecting an array;
a joined string breaks error grouping + display.

This locks in the live error-reporting path (ExceptionWriter) producing a
list. The old PayloadBuilder/writer.Writer classes, which joined frames into
a single "\n"-joined string, were dead code (no live callers) and have been
removed.
"""

from end_point_blank.writers.exception_writer import _format_stacktrace


def _raise_and_capture():
    try:
        def inner():
            raise ValueError("boom")
        inner()
    except ValueError as exc:
        return exc


def test_format_stacktrace_returns_list_not_joined_string():
    exc = _raise_and_capture()

    result = _format_stacktrace(exc)

    assert isinstance(result, list), "stacktrace must be a list, not a string"
    assert not isinstance(result, str)
    assert len(result) > 1, "a real exception should produce multiple frame entries"
    assert all(isinstance(line, str) for line in result)
    assert all("\n" not in line for line in result), (
        "each array entry must be a single frame, not multiple joined lines"
    )
    assert any("ValueError" in line for line in result)
